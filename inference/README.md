# TRELLIS.2 로컬 배치

Task 5의 공식 추론 경로를 감싼 Python/CUDA 배치다. Rust API·수급 계산과 별도 실행하며 GPU 0 하나만 노출한다. 현재 vLLM 통합은 구현되지 않았다. [Sol 서빙 검토](../docs/serving-review.md)를 참고한다.

```bash
docker build -f inference/Dockerfile -t jeju-trellis2:local .
mkdir -p var/generated
docker run --rm --gpus '"device=0"' --network none \
  --user "$(id -u):$(id -g)" --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,exec,size=8g \
  --mount "type=bind,src=$PWD/var/models,dst=/models,readonly" \
  --mount "type=bind,src=$PWD/var/upstream/TRELLIS.2/assets/example_image/T.png,dst=/input.png,readonly" \
  --mount "type=bind,src=$PWD/var/generated,dst=/outputs" \
  jeju-trellis2:local --image /input.png --output /outputs/upstream-smoke \
  --seed 42 --image-source 'microsoft/TRELLIS.2 assets/example_image/T.png' \
  --image-license MIT
```

예제 경로는 고정한 upstream 소스를 내려받은 개발 작업 공간 기준이다. 실제 사용 시 입력 mount와 `--image-source`, `--image-license`를 해당 사진에 맞게 지정한다. 컨테이너에 Hugging Face 토큰이나 DB 자격증명은 전달하지 않는다. 모델은 먼저 [다운로드 스크립트](../scripts/models.sh)로 받아 검증한다.

출력 디렉터리가 이미 있으면 중단한다. 성공 시 `asset.glb`, `metadata.json`을 남기고, 실패 시 완료 metadata는 남기지 않는다. 실패한 디렉터리는 삭제하지 않고 진단용으로 보존한다. 재시도는 새 출력 경로를 사용한다. 원본 이미지와 모델은 읽기 전용이며 로컬 모델 경로를 임시 pipeline 설정에 연결한다.

기본값은 512 pipeline·목표 10만 면·1024 texture·remesh=false다. `--pipeline`, `--faces`, `--texture-size`, `--seed`로 조정한다. GLB 텍스처는 내장 PNG/JPEG인지 검사한다. 이 값은 첫 시험 예산이며 엔진 FPS 검증 결과가 아니다. 모델/코드 revision, 원본 hash/출처/라이선스, seed·sampler 설정, 실제 면 수, 소요시간, PyTorch/CUDA, 할당/예약 VRAM 최고값, GLB hash를 metadata에 기록한다.

결과 상태는 `generated_unvalidated`다. 시설 ID·실제 미터 scale·축 정렬·회전자 pivot·후면 형상·엔진 import는 별도 검증 대상이다. `facility_id`와 `metres_per_unit`은 확인 전 null이다. 공식 예제 이미지의 성공은 제주 시설 복원 성공을 의미하지 않는다. GLB→USD 및 렌더러는 아직 연결하지 않았다.

기반 이미지는 PyTorch 2.6.0/CUDA 12.4/Python 3.11의 digest에 고정한다. TRELLIS.2·CuMesh·FlexGEMM·nvdiffrast와 submodule은 commit으로 고정하고 FlashAttention 2.7.3 및 일반 Python 의존성은 lock에 고정한다. CUDA 확장은 A6000의 SM 8.6 대상으로 빌드한다. 호스트 드라이버는 변경하지 않는다. upstream 소스와 라이선스는 이미지의 `/opt`에 보존한다.

```bash
python3 inference/check.py
```

위 검사는 CUDA 없이 없는 입력·출력 충돌·추론 실패 시 metadata 부재와 빈 GLB 거부를 확인한다. 최종 GLB 검사는 실제 BIN/텍스처 buffer 범위와 PNG/JPEG decode, trimesh 재로딩 후 유한한 삼각형 메시를 확인한다. 완전한 glTF 적합성/엔진 import 검증을 뜻하지 않는다.

2026-09-29 공식 `T.png` 예제로 GPU 0 오프라인 실행에 성공했다. `var/generated/upstream-smoke-03/asset.glb`는 8,186,968바이트·96,390면·내장 이미지 2개이며 최종 검사도 통과했다. metadata 기록 기준 모델 로딩/추론/내보내기 137.70초, PyTorch allocator 최고 할당 2.622GiB·예약 2.916GiB다. CUDA 외부 할당을 포함한 전체 GPU 메모리 측정값은 아니다. 실패한 두 실행에는 완료 metadata가 없음을 확인했다. 로그는 `var/verification/trellis-inference-03.log`, `trellis-final-validation.log`; 결과 조건과 hash는 같은 출력 폴더의 `metadata.json`이다.

2026-09-30 실제 제주 Commons 신창09 원본 사진도 GPU0에서 실행했다. 다운로드한 TRELLIS.2/DINOv3/RMBG/decoder가 사용됐고97,616면·5,890,860bytes GLB를156.99초에 생성했다. 결과가 전경 등대에 대응해 터빈 대체 자산으로 채택하지 않았다. `var/generated/jeju-photo-sinchang09-original-01/metadata.json` 및 `.omo/evidence/local-photo-inference.md`에 원본/모델 hash·출처·GPU 샘플·품질 판단을 기록한다. 현재 통합 지도 설비는 코드 기반 추정 geometry이며 이 생성물을 실측 시설로 표시하지 않는다.
