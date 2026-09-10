"""점수 규칙을 쉬운 말로 엑셀에 적는다."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "규칙_점수표_v52.xlsx"

THIN = Border(
    left=Side(style="thin", color="E5E7EB"),
    right=Side(style="thin", color="E5E7EB"),
    top=Side(style="thin", color="E5E7EB"),
    bottom=Side(style="thin", color="E5E7EB"),
)
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=12)
YELLOW = PatternFill("solid", fgColor="FFF2CC")
GREEN = PatternFill("solid", fgColor="C6EFCE")
RED = PatternFill("solid", fgColor="F8CBAD")
GRAY = PatternFill("solid", fgColor="F3F4F6")
BLUE = PatternFill("solid", fgColor="DDEBF7")
WRAP = Alignment(vertical="center", wrap_text=True)
CENTER = Alignment(horizontal="center", vertical="center")


def paint_header(ws, n_cols: int) -> None:
    ws.row_dimensions[1].height = 24
    for col in range(1, n_cols + 1):
        cell = ws.cell(1, col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def paint_body(ws, score_col: int | None = None) -> None:
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=ws.max_column):
        for i, cell in enumerate(row, 1):
            cell.alignment = WRAP
            cell.border = THIN
            if score_col and i == score_col:
                cell.alignment = CENTER
                cell.font = Font(bold=True, size=12)
                val = cell.value
                if isinstance(val, (int, float)):
                    if val > 0:
                        cell.fill = GREEN
                        cell.value = f"+{int(val)}"
                    elif val < 0:
                        cell.fill = RED
                    else:
                        cell.fill = GRAY
                elif isinstance(val, str) and val.startswith("+"):
                    cell.fill = GREEN
                elif isinstance(val, str) and val.startswith("-"):
                    cell.fill = RED
                else:
                    cell.fill = YELLOW


def add_sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], widths: list[int], score_col: int | None = 3):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for row in rows:
        ws.append(row)
    paint_header(ws, len(headers))
    paint_body(ws, score_col)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 32
    return ws


def main() -> None:
    wb = Workbook()

    how = wb.active
    how.title = "이렇게 봐요"
    how["A1"] = "점수 규칙 (쉬운 버전) · 지금 앱 v94"
    how["A1"].font = Font(size=18, bold=True, color="1F4E79")
    how.merge_cells("A1:B1")
    how_lines = [
        "",
        "점수는 아래 항목을 하나씩 보고, 해당되면 더하거나 뺍니다.",
        "노란/초록/빨간 칸이 점수입니다. 이 칸만 고쳐서 알려주셔도 됩니다.",
        "",
        "순서",
        "1. 「기본 점수」부터 「단기 급상승」까지 더합니다.",
        "2. 더한 점수를 %로 바꿉니다. (15점이면 50%)",
        "3. %로 매수 / 매도 / 홀딩을 정합니다.",
        "4. 홀딩이면 끝입니다. 매수나 매도면 옵션 점수를 더하고 한 번 더 봅니다.",
        "",
        "시트 안내",
        "기본 점수 — 차트·지표로 매기는 점수",
        "옵션 점수 — 미국 주식만, 매수/매도일 때 추가",
        "매수 매도 기준 — 몇 %면 매수이고 몇 %면 매도인지",
        "",
        "참고: 6개월 상승률은 180일 전 종가 대비입니다. 그날이 주말·휴장이면 근처 거래일 종가를 씁니다.",
        "3개월 이상 조회에서 1개월 창(상승선·1개월 추세)은 일봉이 아니라 1개월 조회와 같은 1시간봉 30일을 씁니다.",
    ]
    for i, line in enumerate(how_lines, 2):
        how[f"A{i}"] = line
        if line in ("순서", "시트 안내"):
            how[f"A{i}"].font = Font(bold=True, color="1F4E79", size=13)
        how[f"A{i}"].alignment = WRAP
    how.column_dimensions["A"].width = 78

    add_sheet(
        wb,
        "기본 점수",
        ["항목", "이럴 때", "점수"],
        [
            ["기본", "시작할 때 항상 줌", 15],
            ["추세", "조회 기간이 1~2개월이고, 상승 추세", -1],
            ["추세", "조회 기간이 1~2개월이고, 하락 추세", 1],
            ["추세", "조회 기간이 3개월 이상이고, 하락 추세 (눌림)", 1],
            ["추세", "조회 기간이 3개월 이상이고, 상승 추세 (고점 추격)", -1],
            ["추세", "횡보일 때", 0],
            ["하락 추세선 근접", "하락 추세선에 근접 했을 때, 돌파하면 무효", -1],
            ["상승 추세선 근접", "상승 추세선 근접 했을 때, 이탈하면 무효", 1],
            ["추세선 방향", "하락 추세선 상승 추세선 모두 하락이면서 현재가가 하락 추세선 근접했을때, 하락 추세선 돌파시 무효", -1],
            ["추세선 방향", "하락 추세선 상승 추세선 모두 하락이면서 현재가가 상승 추세선 근접했을 때, 상승 추세선 이탈시 무효", 1],
            ["하방향 1개월 추세선 고려", "(3개월,6개월,1년 조회에만 적용) 하락 추세선 상승 추세선 모두 하락이면서 해당 시점에 1개월(1시간봉기준)조회시 상승추체선이 상방향일 때, 상방향 아니면 무효", 1],
            ["상방향 1개월 추세선 고려", "(3개월,6개월,1년 조회에만 적용) 하락 추세선 상승 추세선 모두 상승이면서 해당 시점에 1개월(1시간봉기준)조회시 상승추체선이 하방향일 때, 상방향 아니면 무효", 1],
            ["지지 근접", "지지선 바로 옆이고 강도 4 이상일 때", 1],
            ["지지 이탈", "지지 이탈 후 다음 지지선이 현재가 보다 뚜렷이 아래에 있을 때", -2],
            ["저항 근접", "저항선 바로 옆이고, 강도 4 이상일 때", -1],
            ["최대 매물 (POC)", "현재가가 거래가 가장 많았던 가격 근접 했을 때, 이탈 시 무효", 1],
            ["밸류 하단 (VAL)", "현재가가 싼 구간 아래이고, 상승 추세일 때", 1],
            ["밸류 상단 (VAH)", "현재가가 비싼 구간 위, 돌파시 무효", -1],
            ["RSI", "35 이하 (너무 많이 떨어짐)", 1],
            ["RSI", "70 이상 (너무 많이 오름)", -1],
            ["20일선", "현재가가 20일선 근처일때, 20일선 완전 이탈시 무효", 1],
            ["60일선", "현재가가 60일선 근처일때, 60일선 완전 이탈시 무효", 1],
            ["180일선", "현재가가 장기 이평 근처일 때. 6개월 조회는 180일선, 1년 조회는 300일선, 완전이탈시 무효", 1],
            ["20일선 60일선 교차", "20일선 방향이 하방으로 떨어지면서 60일선 아래로 떨어지기 시작할때, 떨어지고 4봉이상 지나면 무효", -1],
            ["1개월 상승률", "한 달 동안 70% 이상 오름", -1],
            ["1개월 하락률", "한 달동안 10% 이상 20%미만 하락 했을 때", 1],
            ["1개월 하락률", "한 달 동안 20% 이상 30% 미만 떨어짐", 2],
            ["1개월 하락률", "한 달 동안 30% 이상 떨어짐", 3],
            ["6개월 상승률", "6개월 동안 800% 이상 오름", -3],
            ["6개월 상승률", "6개월 동안 600% 이상 오름, 횡보 추세나 하락 추세시 무효", -2],
            ["6개월 상승률", "6개월 동안 300% 이상 600% 미만 오름, 횡보 추세나 하락 추세시 무효", -1],
            ["단기 급상승", "1개 봉만에 20% 이상 상승했을 시", -1],
        ],
        [18, 72, 10],
    )

    add_sheet(
        wb,
        "옵션 점수",
        ["항목", "이럴 때", "점수"],
        [
            ["옵션", "기존 결과가 홀딩이면 옵션은 보지 않음", 0],
            ["옵션", "기존이 매도인데, 만기가 14일 안이고, 위쪽에 콜 벽이 두껍고 아래 풋 벽이 얇음", -1],
            ["옵션", "기존이 매도인데, 반대로 아래 풋 벽이 두껍고 위 콜 벽이 얇음", 1],
            ["옵션", "기존이 매도인데, 벽이 멀거나 둘 다 얇음", 0],
            ["옵션", "기존이 매수인데, 아래 풋 벽이 얇고 위 콜 벽이 두꺼움", -1],
            ["옵션", "기존이 매수인데, 위와 다르면", 0],
        ],
        [12, 78, 10],
    )
    opt = wb["옵션 점수"]
    opt["A9"] = "참고: 미국 주식, 오늘 조회만. 근처는 현재가에서 5% 안, 멀면 8% 밖."
    opt["A9"].font = Font(italic=True, color="666666")
    opt.merge_cells("A9:C9")

    cuts = wb.create_sheet("매수 매도 기준")
    cuts.merge_cells("A1:B1")
    cuts.merge_cells("D1:E1")
    cuts["A1"] = "코인"
    cuts["D1"] = "주식"
    for cell in (cuts["A1"], cuts["D1"]):
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = THIN
    cuts.append([])
    cuts["A2"] = "합산 %"
    cuts["B2"] = "제안"
    cuts["D2"] = "합산 %"
    cuts["E2"] = "제안"
    for col in (1, 2, 4, 5):
        cell = cuts.cell(2, col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = THIN
    crypto_rows = [
        ["80% 이상", "강한 매수"],
        ["73% 이상 ~ 80% 미만", "매수"],
        ["67% 이상 ~ 73% 미만", "약한 매수"],
        ["37% 초과 ~ 67% 미만", "홀딩"],
        ["33% 초과 ~ 37% 이하", "약한 매도"],
        ["27% 초과 ~ 33% 이하", "매도"],
        ["27% 이하", "강한 매도"],
    ]
    stock_rows = [
        ["73% 이상", "강한 매수"],
        ["67% 이상 ~ 73% 미만", "매수"],
        ["60% 이상 ~ 67% 미만", "약한 매수"],
        ["33% 초과 ~ 60% 미만", "홀딩"],
        ["27% 초과 ~ 33% 이하", "약한 매도"],
        ["20% 초과 ~ 27% 이하", "매도"],
        ["20% 이하", "강한 매도"],
    ]
    fills = (
        GREEN,
        GREEN,
        PatternFill("solid", fgColor="E2EFDA"),
        YELLOW,
        RED,
        RED,
        PatternFill("solid", fgColor="F4B183"),
    )
    for i, ((cp, ca), (sp, sa), fill) in enumerate(zip(crypto_rows, stock_rows, fills), 3):
        cuts.cell(i, 1, cp)
        cuts.cell(i, 2, ca)
        cuts.cell(i, 4, sp)
        cuts.cell(i, 5, sa)
        for col in (1, 2, 4, 5):
            cell = cuts.cell(i, col)
            cell.fill = fill
            cell.border = THIN
            cell.alignment = CENTER
            if col in (2, 5):
                cell.font = Font(bold=True, size=12)
        cuts.row_dimensions[i].height = 28
    cuts["A11"] = "점수를 %로 바꾸는 법: 0점이면 0%, 15점(기본)이면 50%, 31점이면 100%."
    cuts["A11"].font = Font(italic=True, color="666666")
    cuts.merge_cells("A11:E11")
    cuts["A12"] = "가까운 가격: 하루 변동폭의 약 55%, 또는 주가의 1.0% 중 더 큰 값."
    cuts["A12"].font = Font(italic=True, color="666666")
    cuts.merge_cells("A12:E12")
    cuts.column_dimensions["A"].width = 28
    cuts.column_dimensions["B"].width = 14
    cuts.column_dimensions["C"].width = 4
    cuts.column_dimensions["D"].width = 28
    cuts.column_dimensions["E"].width = 14

    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    wb.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
