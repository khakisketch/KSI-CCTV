# 사용자 모델 업로드 설계

## 목표

고객 또는 파트너가 만든 ONNX 모델을 KSI-CCTV에 등록해 카메라별 감지 이벤트에 연결할 수 있게 합니다.

## 1차 지원 범위

초기 버전에서는 다음만 지원합니다.

- YOLO 계열 ONNX 모델
- 단일 이미지 입력 모델
- bbox detection 출력
- metadata/classes YAML 제공

지원하지 않음:

- PyTorch `.pt`
- pickle 계열 모델
- Python 코드 실행이 필요한 모델
- segmentation/pose/custom operator 모델

## 업로드 파일

```text
customer_model.onnx
metadata.yaml
```

metadata 예시:

```yaml
model_name: customer_ppe_v1
model_type: yolo_onnx
input_size: [640, 640]
postprocess_mode: auto
classes:
  - person
  - helmet
  - vest
```

## 보안 원칙

사용자 모델 업로드는 보안 민감 기능입니다.

필수 원칙:

- ONNX만 허용
- 파일 크기 제한
- 파일명 sanitize
- 업로드 디렉토리 격리
- ONNX shape 검사
- inference timeout
- 모델 로드 실패 시 전체 서비스 장애 방지
- 업로드/활성화/삭제 감사 로그
- 관리자 권한 필요

## 검증 절차

1. 확장자 검사
2. 파일 크기 검사
3. ONNX Runtime 로드 검사
4. input/output metadata 검사
5. class count 검증
6. synthetic sample image smoke test
7. 성공 시 disabled 상태로 등록
8. 운영자가 카메라에 할당 후 활성화

## 향후 UI

모델 관리 화면:

- 모델 업로드
- 클래스 목록 확인
- 샘플 이미지 테스트
- 활성화/비활성화
- 카메라 할당
- 롤백/삭제
