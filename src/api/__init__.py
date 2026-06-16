"""KSI-CCTV 엔진 API (FastAPI).

헤드리스 엔진이 REST/WS로 이벤트·설정·카메라·모델·정책·헬스를 노출하는 단일 진입점.
프론트엔드(웹/모바일)·외부 연동이 이 API만 바라보도록 설계한다(설치형 제품화 P1).

보안: 모든 보호 엔드포인트는 fail-closed API 키 인증(src.api.auth) + 시크릿 마스킹(service)을 거친다.
"""
