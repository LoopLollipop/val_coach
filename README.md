## Val_coach  
### Riot의 게임 'Valorant' 로그 분석 및 코칭 프로그램
#### val_logs_to_json.py
- 목적 : Riot API에서 Valorant 전적(최근 1경기)를 가져와 JSON 배열로 전환
- 기능 :
1. Riot ID에서 PUUID 조회
2. 활성 shards 자동 감지(asia 서버 유저 지원용)
3. 최근 매치 ID 목록 조회, 매치 상세 호출, 핵심 이벤트(Kill, death, plant, defuse등) 추출
4. 시간순으로 정렬하여 정해진 형식으로 저장
- 비고 :
1. 현재 개인용 API는 Valorant 로그를 미지원. 다른 방법 모색 필요
2. 더하여 Riot api(개발용)은 24시간마다 권한이 사라짐.
   
#### make_fake_val_log.py
- 목적 : 개인용 Riot API가 Valorant를 지원하지 않아 임시로 랜덤로그를 생성해주는 기능이 필요해짐.
- 기능 :
  1. 맵/무기/라운드/킬/식물·해제 이벤트를 그럴듯하게 랜덤 생성
  2. 재현성을 위한 --seed 지원
  3. 출력: sample.json

#### analyze_valorant.py
- 목적 : 이벤트 로그(JSON)를 **LLM(Gemini)**로 분석하여 요약/코칭/하이라이트/지표를 담은 리포트 JSON 파일 생성
- 기능 :
1. 입력 로그 정렬 및 샘플링으로 토큰 절약(무료 버전 사용으로 인한 선택)
2. 모델 응답을 견고하게 파싱: 코드펜스 제거, 다중 JSON 후보 스캔, 미완성 JSON 자동 복구(JSON 파일 미생성 버그가 다수 있었기에 보완)
3. 실패 시 축약 프롬프트로 재시도 및 원본 응답 저장
4. 이벤트 로그(JSON)를 LLM(Gemini)로 분석하여 요약/코칭/하이라이트/지표를 담은 리포트 JSON 파일 생성

#### Github에 미게시한 파일
1. .env(환경변수) : API KEY 정보 들어있음

#### 작동 흐름
- (Riot API) val_logs_to_json.py → sample.json → analyze_valorant.py (Gemini) → report.json
- val_logs_to_json.py 작동 불가로 해당 단계에 make_fake_val_log.py 대체.

#### 결과물 사진
<img width="1282" height="492" alt="image" src="https://github.com/user-attachments/assets/8e4f8497-4bfa-450e-9fc6-d5dd5ba4ba5f" />
