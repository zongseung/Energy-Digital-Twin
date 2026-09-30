FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel@sha256:0cf3402e946b7c384ba943ee05c90b4c5a4a05227923921f2b0918c011cfaf56

ENV DEBIAN_FRONTEND=noninteractive TORCH_CUDA_ARCH_LIST=8.6 MAX_JOBS=4
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.8.11
COPY inference/requirements.lock /tmp/requirements.lock
RUN uv pip install --system -r /tmp/requirements.lock
RUN uv pip install --system --no-build-isolation --no-deps flash-attn==2.7.3

WORKDIR /opt
RUN git clone https://github.com/microsoft/TRELLIS.2.git trellis2 \
    && cd trellis2 && git checkout 75fbf0183001ed9876c8dbb35de6b68552ee08bd \
    && git submodule update --init --recursive
RUN git clone https://github.com/JeffreyXiang/CuMesh.git cumesh \
    && cd cumesh && git checkout 12289e1062f0603f2f0d0771b02e1395d247f26f \
    && git submodule update --init --recursive \
    && uv pip install --system --no-build-isolation --no-deps .
RUN git clone https://github.com/JeffreyXiang/FlexGEMM.git flexgemm \
    && cd flexgemm && git checkout 6dd94a859c26ee8246888502eada3dd8ad85532e \
    && git submodule update --init --recursive \
    && uv pip install --system --no-build-isolation --no-deps .
RUN git clone https://github.com/NVlabs/nvdiffrast.git nvdiffrast \
    && cd nvdiffrast && git checkout 253ac4fcea7de5f396371124af597e6cc957bfae \
    && uv pip install --system --no-build-isolation --no-deps .
RUN uv pip install --system --no-build-isolation --no-deps /opt/trellis2/o-voxel
RUN uv pip install --system --no-deps kornia==0.8.1 kornia-rs==0.1.9 \
    && uv pip check --system

ENV PYTHONPATH=/opt/trellis2 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/huggingface \
    TORCH_EXTENSIONS_DIR=/tmp/torch-extensions PYTHONUNBUFFERED=1 \
    TRITON_CACHE_DIR=/tmp/triton FLEX_GEMM_AUTOTUNE_CACHE_PATH=/tmp/flex-gemm.json \
    FLEX_GEMM_AUTOSAVE_AUTOTUNE_CACHE=0 \
    ATTN_BACKEND=flash_attn PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
WORKDIR /opt/runner
COPY inference/run.py /opt/runner/run.py
COPY models/manifest.tsv /opt/runner/manifest.tsv
ENTRYPOINT ["python", "/opt/runner/run.py"]
