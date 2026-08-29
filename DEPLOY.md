# 외부에서 쓰는 법

이 앱은 원래 `http://localhost:8501` 이라 **이 PC에서만** 열립니다.
밖에서 쓰려면 아래 셋 중 하나입니다.

공유기 포트포워딩으로 8501을 인터넷에 직접 여는 방식은 쓰지 마세요.

---

## 방법 1. ngrok (지금 바로, 이 PC가 켜져 있어야 함)

이미 이 컴퓨터에 ngrok가 있습니다. 분석은 계속 이 PC에서 돌아가고,
ngrok가 임시 인터넷 주소만 붙여 줍니다.

1. `run.bat` 으로 앱을 켭니다. (`http://localhost:8501`)
2. 외부용 비밀번호를 넣습니다.

```powershell
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
notepad .streamlit\secrets.toml
```

`APP_PASSWORD` 를 원하는 값으로 바꾼 뒤 저장하고, Streamlit을 한 번 재시작합니다.

3. `deploy-ngrok.bat` 을 실행합니다.
4. 검은 창에 나오는 `https://xxxx.ngrok-free.app` 주소를 폰·다른 PC 브라우저에 넣습니다.
5. ngrok 경고 페이지가 뜨면 **Visit Site** 를 누른 뒤, 앱 비밀번호를 입력합니다.

주의
- 이 PC가 꺼지거나 `run.bat` / ngrok 창을 닫으면 밖에서 안 열립니다.
- 무료 ngrok는 주소를 켤 때마다 바뀔 수 있습니다.
- 주소를 아는 사람은 누구나 들어오므로 비밀번호를 꼭 쓰세요.

계정: https://dashboard.ngrok.com

---

## 방법 2. Streamlit Community Cloud (컴퓨터 꺼도 유지)

GitHub에 코드를 올리면 Streamlit이 서버에서 실행합니다.
컴퓨터가 꺼져 있어도 `https://xxxx.streamlit.app` 로 들어올 수 있습니다.

1. https://share.streamlit.io 접속 (GitHub 계정으로 로그인)
2. **Create app** → **Use existing repo** (또는 New app)
3. Repository: `소유자/trade-advisor`
4. Branch: `main`
5. Main file path: `app.py`
6. Advanced: Python **3.12** (3.14로 두면 패키지가 깨질 수 있음)
7. Secrets에 아래를 넣습니다.

```
APP_PASSWORD = "원하는비밀번호"
GITHUB_TOKEN = "ghp_..."
```

다른 기기·리부트 후에도 즐겨찾기를 이어가려면 `GITHUB_TOKEN` 이 필요합니다.

1. https://github.com/settings/tokens → **Generate new token (classic)**
2. Note: `trade-advisor`, Expiration: 원하는 기간, 권한은 **gist** 만 체크
3. 생성 후 `ghp_` 로 시작하는 값을 복사 (한 번만 보임)
4. Streamlit Cloud 앱 → **Manage app → Settings → Secrets** 에 위처럼 붙여 넣고 Save
5. **Reboot app**
6. 즐겨찾기가 있는 기기에서 앱을 열고, 사이드바 **지금 클라우드에 저장**
7. 폰·다른 PC에서는 사이드바 저장 코드를 넣고 **이 코드로 불러오기**

토큰은 저장소에 올리지 마세요. gist 권한만 있으면 되고, 앱이 `trade-advisor-prefs.json` 비공개 Gist를 만들거나 찾아 씁니다.

8. Deploy. 끝나면 `https://xxxx.streamlit.app` 주소가 생깁니다.

클라우드에서 한국 종목은 Yahoo `.KS`/`.KQ` 폴백을 씁니다. 시세 사이트가 클라우드 IP를 막으면 검색이 실패할 수 있습니다.

---

## 방법 3. Render / Railway 같은 서버

GitHub 연결 후 `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
형태로 실행합니다. 항상 켜 둘 수 있지만 무료 플랜은 잠자기(sleep)가 있습니다.

---

## 비교

| | ngrok | Streamlit Cloud | 홈 공유기 포트 개방 |
|---|---|---|---|
| 컴퓨터 꺼도 되나 | 아니오 | 예 | 예(공유기+PC 켜져 있어야) |
| 지금 가능한가 | 예 | 저장소 연결됨 | 비추천 |
| 주소 고정 | 유료면 가능 | 예 | 공인 IP 변동 |
| 보안 | 비밀번호 + HTTPS | 비밀번호 | 해킹 위험 |
