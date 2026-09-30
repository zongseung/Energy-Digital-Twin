# GPU serving and rendering review

Checked 2026-09-29. Scope: read-only architecture review; no deployment or host-driver changes. Ponytail recommendation: retain the existing Rust API and TRELLIS.2 batch worker, add Nginx when there is a browser-facing surface, and add genuine vLLM serving only for a specified supported workload.

Implementation update (2026-09-30): the [browser Mock](mock-rendering.md) now runs behind pinned Nginx on loopback 8080; native-client HTTP/WS proxying passed. [Kit 106.5 on GPU 1](omniverse-mock.md) produced a real RTX PNG/USD on unchanged driver 535.183.01. This is a batch Mock render, not WebRTC streaming or TRELLIS serving via vLLM. The workload decision below is still open.

## vLLM support decision

The latest published releases checked through GitHub's release API are [vLLM v0.30.0](https://github.com/vllm-project/vllm/releases/tag/v0.30.0) (2026-09-22) and [vLLM-Omni v0.30.0](https://github.com/vllm-project/vllm-omni/releases/tag/v0.30.0) (2026-09-25). Neither the versioned [vLLM supported-model list](https://docs.vllm.ai/en/v0.30.0/models/supported_models/) nor the [vLLM-Omni supported-pipeline list](https://docs.vllm.ai/projects/vllm-omni/en/v0.30.0/models/supported_models/) lists TRELLIS.2. The current `latest` lists were also checked.

TRELLIS.2 is a custom image-to-3D pipeline: background removal and image conditioning, sparse-structure flow/decoder, shape and texture structured-latent flows/decoders, then mesh/PBR export. Its [pinned upstream implementation](https://github.com/microsoft/TRELLIS.2/blob/75fbf0183001ed9876c8dbb35de6b68552ee08bd/trellis2/pipelines/trellis2_image_to_3d.py) loads multiple native models through `pipeline.json`. DINOv3 is its conditioning encoder; serving that encoder alone does not serve this full pipeline. Neither `vllm serve microsoft/TRELLIS.2-4B` nor changing the HTTP wrapper establishes supported 3D inference.

vLLM's Transformers backend has compatibility requirements for model structure and attention; it is not an arbitrary Python pipeline executor. vLLM-Omni does provide a [custom pipeline extension mechanism](https://docs.vllm.ai/projects/vllm-omni/en/latest/features/custom_pipeline/), including worker extensions and a Diffusers adapter. This makes an integration project possible in principle, not an existing TRELLIS.2 implementation. Such work must prove sparse tensor/native-extension execution, request/result adaptation for GLB artifacts, cancellation/concurrency, and output parity against upstream; it must not be presented as native vLLM acceleration.

**Actual vLLM serving is blocked by the absence of a selected supported model and application workload.** If TRELLIS.2 itself must run inside vLLM-Omni, the blocker is an implemented and verified custom integration. Keep Task 5's upstream batch path until that scope is chosen.

One needed choice: **Which exact model repository and task should vLLM serve: a separate text/vision-language assistant, or a custom TRELLIS.2 integration?** No replacement model has been selected.

## Driver and GPU compatibility

Read-only `nvidia-smi` confirmed two RTX A6000 GPUs, each 49140 MiB, driver **535.183.01**. The existing `var/verification/trellis-cuda-probe.log` shows a successful Torch 2.6.0/CUDA 12.4 GPU tensor operation. This proves basic compute, not native-extension builds or a complete 3D run. The draft inference Dockerfile uses the matching pinned PyTorch/CUDA base; upstream's [installation instructions](https://github.com/microsoft/TRELLIS.2/blob/75fbf0183001ed9876c8dbb35de6b68552ee08bd/README.md) recommend CUDA 12.4 and default to Torch 2.6.0.

The [vLLM v0.30.0 GPU instructions](https://docs.vllm.ai/en/v0.30.0/getting_started/installation/gpu/) require NVIDIA compute capability >=7.5; A6000's 8.6 clears that hardware gate. Current default binaries use CUDA 12.9; CUDA 12.8 and 13.0 variants are documented. Docker images include compatibility libraries for eligible older-driver systems.

[NVIDIA minor compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) permits CUDA 12.x on drivers >=525 with restrictions; newer PTX/JIT and driver features can still fail. CUDA 13.x normally needs >=580. [Forward compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html) lists R535-compatible packages, but eligibility is restricted to data-center GPUs and selected NGC Server Ready RTX SKUs. The exact A6000 system's eligibility and real vLLM kernel/JIT execution remain unverified. Do not infer compatibility from a version number or from the Torch 12.4 probe. Pin an isolated image and test the chosen model on 535 first; no host upgrade is part of this recommendation.

`docker ps` exposed only names/images/ports; no container environments or credentials were read. Existing `vllm/vllm-openai:v0.10.2` and `v0.9.2` images were found locally; no running container had a vLLM-named image. Other projects' containers were untouched. No port or GPU ownership should be inferred from image presence.

## Nginx and rendering

Recommended flow:

```text
Browser -> Nginx HTTPS -> static frontend / generated GLB
                       -> Rust HTTP + /api/v1/jeju/ws on 127.0.0.1:8090
Rust -> existing Redis on 127.0.0.1:6380
Rust -> SSH bridge tunnel on 127.0.0.1:18091
GPU 0 -> pinned TRELLIS.2 batch -> durable GLB / validated USD conversion
GPU 1 -> chosen Kit renderer -> separate WebRTC media path to browser
```

Nginx can serve frontend assets and GLB files, terminate TLS, and proxy existing HTTP/WebSocket API routes. Configure WebSocket Upgrade/Connection headers and a suitable read timeout or heartbeat, as described in [Nginx's official WebSocket guide](https://nginx.org/en/docs/http/websocket.html). Preserve application origin validation; publishing the API also requires a defined access policy. Nginx needs no GPU and does not render meshes.

Kit's web UI/HTTP and verified HTTP/WebSocket signaling can sit behind an HTTP proxy. **WebRTC media does not travel through an Nginx HTTP location.** [Kit streaming settings](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.livestream.app/latest/Overview.html) distinguish TCP signaling from UDP media. [Kit 108 migration](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/migration_108.html) uses defaults TCP 49100 / UDP 47998; earlier Kit versions differ. Select and pin the actual Kit version before opening ports. [Kit network guidance](https://docs.omniverse.nvidia.com/ovas/latest/architecture/network-load-balancers.html) also identifies ICE-related ranges. Public endpoint/ICE setup, reachable UDP, and STUN/TURN when required by NAT are separate deployment work; HTTPS proxy success proves none of them.

What is implementable now: a minimal Nginx HTTP/WS front for the Rust API and real generated assets when available. What remains blocked: working renderer image/version, scene import and GPU 1 execution, streaming extension/client, selected network path and access policy. At review time no `renderer/` implementation existed and the draft inference image was not yet verified end to end. Do not add an empty renderer upstream or claim a streamed scene before those checks pass. Keep API and Redis loopback-only until the intended exposure is configured; keep GPU 0 batch generation and GPU 1 rendering ownership explicit.
