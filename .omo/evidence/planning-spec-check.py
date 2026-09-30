from pathlib import Path
import hashlib
import json
import re
import subprocess

root = Path(__file__).resolve().parents[2]
path = root / "jeju_power_grid_digital_twin_design.md"
text = path.read_text()
base = subprocess.check_output(["git", "show", "HEAD:jeju_power_grid_digital_twin_design.md"], cwd=root, text=True)
diff = subprocess.check_output(["git", "diff", "--", path.name], cwd=root, text=True)
latest = text.split("## 19. ", 1)[1]
checks = {
    "early goals and MVP use current scene": all(s in text[:text.index("## 5.")] for s in ["현재 장면", "PV 한 곳", "방위각", "경사각", "같은 카메라 구도"]),
    "problem and beneficiaries": all(s in text for s in ["해결할 문제", "시설 운영자", "정책·사업 담당자", "주민·평가자"]),
    "scene is not electrical boundary": "장면 경계는 전력 수지 경계가 아니므로" in latest,
    "geometry sources and same-camera acceptance": all(s in latest for s in ["도면", "현장 사진", "행열", "1% 이내", "각 2° 이내", "카메라 설정"]),
    "shared asset specifications": "렌더러·통계·모델의 시설 ID와 제원 hash 일치" in latest,
    "weather source time and quality": all(s in latest for s in ["awsTmp", "awsReh", "awsPcpHr1", "150초", "15분", "QC 통과"]),
    "irradiance separately sourced with units": all(s in latest for s in ["icsr", "MJ/m²", "100W/m²", "5분 실측이 아니다"]),
    "PV physical chain and validation": all(s in latest for s in ["POA", "Tcell", "Pdc", "인버터", "야간0", "정격 AC 상한"]),
    "statistics and missing history limits": all(s in latest for s in ["결측률", "유효시간", "MAE", "시간순", "이력을 저장하지 않으므로"]),
    "no false curtailment": "실제 출력제어량으로 명명하지 않는다" in latest,
    "observed estimated planned modeled labels": "관측/추정/계획/모형 라벨 구분" in latest,
    "existing stack reuse": all(s in text for s in ["기존 GIS", "Rust", "추가 브로커·플랫폼·모델 서비스 없이"]),
}
original_history = base.split("## 14. ", 1)[1].strip()
preserved_history = text.split("## 14. ", 1)[1].split("\n## 19.", 1)[0]
preserved_history = re.sub(r"\n> \*\*14–18절[^\n]*\n", "", preserved_history).strip()
checks["historical sections 14–18 preserved verbatim"] = preserved_history == original_history
added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
links = [target for target in re.findall(r"\]\(([^)]+)\)", added) if not target.startswith(("https://", "http://", "#"))]
checks["all added local links resolve"] = all((root / target).is_file() for target in links)
spec = json.loads((root / "renderers/twin/grid-spec.json").read_text())
checks["scene and generic PV facts match source"] = spec["terrain_bbox_lon_lat"] == [126.145, 33.31, 126.40, 33.435] and (spec["pv_example"]["rows"], spec["pv_example"]["columns"], spec["pv_example"]["tilt_deg"]) == (4, 8, 25)
result = subprocess.run(["git", "diff", "--check", "--", path.name], cwd=root, capture_output=True, text=True)
checks["git diff --check"] = result.returncode == 0
for name, passed in checks.items():
    print(f"{'PASS' if passed else 'FAIL'}: {name}")
print(f"document_sha256: {hashlib.sha256(path.read_bytes()).hexdigest()}")
print(f"added_local_links: {sorted(set(links))}")
print(f"checks: {sum(checks.values())}/{len(checks)}")
assert all(checks.values()), checks
