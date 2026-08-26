# 매매시점 제안

원하는 **날짜 시점**의 일봉만 사용해 추세선, 지지/저항, 주요 매물대를 계산하고
**매수 / 매도 / 홀딩**을 제안하는 Streamlit 앱입니다.

지원 시장
- 한국 주식 (종목명·코드 검색)
- 미국 주식 (티커)
- 코인: BTC, ETH, SOL, XRP, ONDO 및 심볼 직접 입력

## 실행

```powershell
cd C:\Users\User\trade-advisor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 이 열립니다.

밖에서 쓰려면 [DEPLOY.md](DEPLOY.md) 를 보세요.

Streamlit Community Cloud에 올릴 때는 Python **3.12**, 진입 파일 `app.py` 를 고르세요.

## 계산 요약

- **시점**: 선택한 날짜의 종가까지. 그 이후 봉은 쓰지 않습니다.
- **추세선**: 최근 스윙 고점·저점 2개를 잇습니다.
- **지지·저항**: 스윙 가격 군집 + 매물대 노드.
- **매물대**: 각 일봉의 고가~저가 구간에 그날 거래량을 나눠 쌓은 가격대 히스토그램.
  POC(최대 매물), VAL/VAH(거래량 70% 구간)를 표시합니다.
- **신호**: 추세, 지지/저항 이격, RSI, 손익비를 점수로 합산합니다.

투자 자문이 아니며, 일봉 근사라 분봉 매물대와는 차이가 있습니다.
