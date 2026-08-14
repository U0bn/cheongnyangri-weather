import os
import math
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import unquote
from PIL import Image, ImageDraw, ImageFont


# =========================================================
# 기본 설정
# =========================================================

KMA_API_KEY = unquote(os.environ["KMA_API_KEY"])
AIRKOREA_API_KEY = unquote(os.environ["AIRKOREA_API_KEY"])

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# 청량리동
NX = 61
NY = 127

AIR_STATION = "동대문구"

KST = ZoneInfo("Asia/Seoul")

OUTPUT_FILE = "weather_card.png"


# =========================================================
# 폰트
# =========================================================

def get_font(size, bold=False):

    candidates = []

    if bold:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.otf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
        ]

    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


# =========================================================
# 기상청 발표 시각
# =========================================================

def get_base_datetime():

    now = datetime.now(KST)

    base_times = [
        "0200",
        "0500",
        "0800",
        "1100",
        "1400",
        "1700",
        "2000",
        "2300"
    ]

    available = []

    for bt in base_times:

        hour = int(bt[:2])

        dt = now.replace(
            hour=hour,
            minute=0,
            second=0,
            microsecond=0
        )

        if now >= dt + timedelta(minutes=15):
            available.append(dt)

    if available:
        base_dt = max(available)

    else:

        yesterday = now - timedelta(days=1)

        base_dt = yesterday.replace(
            hour=23,
            minute=0,
            second=0,
            microsecond=0
        )

    return (
        base_dt.strftime("%Y%m%d"),
        base_dt.strftime("%H%M")
    )


# =========================================================
# 기상청 날씨
# =========================================================

def get_weather():

    base_date, base_time = get_base_datetime()

    url = (
        "https://apis.data.go.kr/1360000/"
        "VilageFcstInfoService_2.0/getVilageFcst"
    )

    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": "1",
        "numOfRows": "1000",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": NX,
        "ny": NY
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    header = data["response"]["header"]

    if header["resultCode"] != "00":
        raise Exception(
            f'기상청 API 오류: '
            f'{header["resultCode"]} '
            f'{header["resultMsg"]}'
        )

    items = (
        data["response"]
        ["body"]
        ["items"]
        ["item"]
    )

    return items, base_date, base_time


# =========================================================
# 날씨 정리
# =========================================================

def parse_weather(items):

    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")

    hourly = {}

    tmn = None
    tmx = None

    for item in items:

        if item["fcstDate"] != today:
            continue

        time = item["fcstTime"]
        category = item["category"]
        value = item["fcstValue"]

        if time not in hourly:
            hourly[time] = {}

        hourly[time][category] = value

        if category == "TMN":
            try:
                tmn = float(value)
            except:
                pass

        if category == "TMX":
            try:
                tmx = float(value)
            except:
                pass

    if not hourly:
        raise Exception("오늘 예보 데이터가 없습니다.")

    times = sorted(hourly.keys())

    current_hour = now.strftime("%H00")

    future = [
        t for t in times
        if t >= current_hour
    ]

    if future:
        current_time = future[0]
    else:
        current_time = times[-1]

    current = hourly[current_time]

    current_temp = current.get("TMP", "?")

    temps = []
    pops = []
    precip = []

    for time in times:

        values = hourly[time]

        if "TMP" in values:
            try:
                temps.append(float(values["TMP"]))
            except:
                pass

        if "POP" in values:
            try:
                pops.append(int(values["POP"]))
            except:
                pass

        if "PCP" in values:

            amount = values["PCP"]

            if amount not in [
                "강수없음",
                "0",
                "0.0"
            ]:
                precip.append(
                    (time, amount)
                )

    if tmn is None:
        tmn = min(temps) if temps else "?"

    if tmx is None:
        tmx = max(temps) if temps else "?"

    return {
        "current_temp": current_temp,
        "min_temp": tmn,
        "max_temp": tmx,
        "max_pop": max(pops) if pops else 0,
        "precip": precip,
        "hourly": hourly
    }


# =========================================================
# 체감온도
# =========================================================

def wet_bulb_temperature(temp, rh):

    return (
        temp
        * math.atan(
            0.151977
            * math.sqrt(rh + 8.313659)
        )
        + math.atan(temp + rh)
        - math.atan(rh - 1.67633)
        + 0.00391838
        * (rh ** 1.5)
        * math.atan(0.023101 * rh)
        - 4.686035
    )


def apparent_temperature(temp, rh):

    tw = wet_bulb_temperature(temp, rh)

    return (
        -0.2442
        + 0.55399 * tw
        + 0.45535 * temp
        - 0.0022 * (tw ** 2)
        + 0.00278 * tw * temp
        + 3.0
    )


def get_max_apparent_temperature(hourly):

    result = []

    for values in hourly.values():

        if (
            "TMP" not in values
            or "REH" not in values
        ):
            continue

        try:
            temp = float(values["TMP"])
            rh = float(values["REH"])
        except:
            continue

        result.append(
            apparent_temperature(
                temp,
                rh
            )
        )

    return max(result) if result else None


# =========================================================
# 에어코리아
# =========================================================

def get_air_quality():

    url = (
        "https://apis.data.go.kr/"
        "B552584/"
        "ArpltnInforInqireSvc/"
        "getMsrstnAcctoRltmMesureDnsty"
    )

    params = {
        "serviceKey": AIRKOREA_API_KEY,
        "returnType": "json",
        "numOfRows": "100",
        "pageNo": "1",
        "stationName": AIR_STATION,
        "dataTerm": "DAILY",
        "ver": "1.4"
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    items = (
        data.get("response", {})
        .get("body", {})
        .get("items", [])
    )

    if not items:

        return {
            "pm10": None,
            "pm25": None,
            "data_time": "정보없음"
        }

    item = items[0]

    pm10 = item.get("pm10Value")
    pm25 = item.get("pm25Value")

    if pm10 in [None, "", "-"]:
        pm10 = None

    if pm25 in [None, "", "-"]:
        pm25 = None

    return {
        "pm10": pm10,
        "pm25": pm25,
        "data_time": item.get(
            "dataTime",
            "정보없음"
        )
    }


# =========================================================
# 미세먼지 등급
# =========================================================

def pm10_grade(value):

    if value is None:
        return "정보없음"

    try:
        v = int(value)
    except:
        return "정보없음"

    if v <= 30:
        return "좋음"

    if v <= 80:
        return "보통"

    if v <= 150:
        return "나쁨"

    return "매우 나쁨"


def pm25_grade(value):

    if value is None:
        return "정보없음"

    try:
        v = int(value)
    except:
        return "정보없음"

    if v <= 15:
        return "좋음"

    if v <= 35:
        return "보통"

    if v <= 75:
        return "나쁨"

    return "매우 나쁨"


# =========================================================
# 행동 조언
# =========================================================

def make_advice(weather, air, max_feels):

    advice = []

    if weather["precip"]:
        advice.append("우산 챙기기")
    else:
        advice.append("우산 필요 없음")

    pm10 = pm10_grade(air["pm10"])
    pm25 = pm25_grade(air["pm25"])

    if (
        pm10 in ["나쁨", "매우 나쁨"]
        or pm25 in ["나쁨", "매우 나쁨"]
    ):
        advice.append("마스크 권장")

    else:
        advice.append("마스크 불필요")

    if (
        max_feels is not None
        and max_feels >= 33
    ):
        advice.append("더위 대비")

    return " · ".join(advice)


# =========================================================
# 카드 이미지
# =========================================================

def create_weather_card(weather, air):

    now = datetime.now(KST)

    max_feels = (
        get_max_apparent_temperature(
            weather["hourly"]
        )
    )

    pm10_status = pm10_grade(
        air["pm10"]
    )

    pm25_status = pm25_grade(
        air["pm25"]
    )

    precip_bad = bool(
        weather["precip"]
    )

    pm10_bad = pm10_status in [
        "나쁨",
        "매우 나쁨"
    ]

    pm25_bad = pm25_status in [
        "나쁨",
        "매우 나쁨"
    ]

    heat_bad = (
        max_feels is not None
        and max_feels >= 33
    )

    alert = (
        precip_bad
        or pm10_bad
        or pm25_bad
        or heat_bad
    )

    # 이미지
    W = 1080
    H = 1350

    bg = (246, 246, 242)
    dark = (28, 28, 30)
    gray = (110, 110, 115)
    line = (220, 220, 215)

    if alert:
        accent = (205, 62, 54)
        status_text = "오늘 꼭 확인"
    else:
        accent = (60, 120, 82)
        status_text = "외출 무난"

    img = Image.new(
        "RGB",
        (W, H),
        bg
    )

    draw = ImageDraw.Draw(img)

    font_small = get_font(34)
    font_medium = get_font(45)
    font_large = get_font(72, True)
    font_temp = get_font(150, True)
    font_status = get_font(52, True)

    margin = 75

    # 날짜
    date_text = (
        now.strftime("%m월 %d일")
        + " · 청량리동"
    )

    draw.text(
        (margin, 65),
        date_text,
        font=font_small,
        fill=gray
    )

    # 상태
    draw.rounded_rectangle(
        (
            margin,
            130,
            W - margin,
            240
        ),
        radius=32,
        fill=accent
    )

    draw.text(
        (
            margin + 35,
            157
        ),
        status_text,
        font=font_status,
        fill=(255, 255, 255)
    )

    # 현재 기온
    draw.text(
        (margin, 300),
        f'{weather["current_temp"]}°',
        font=font_temp,
        fill=dark
    )

    draw.text(
        (margin + 355, 375),
        "현재 기온",
        font=font_medium,
        fill=gray
    )

    # 최저 최고 체감
    y = 520

    draw.text(
        (margin, y),
        "최저",
        font=font_small,
        fill=gray
    )

    draw.text(
        (margin, y + 45),
        f'{weather["min_temp"]}°',
        font=font_large,
        fill=dark
    )

    draw.text(
        (370, y),
        "최고",
        font=font_small,
        fill=gray
    )

    draw.text(
        (370, y + 45),
        f'{weather["max_temp"]}°',
        font=font_large,
        fill=dark
    )

    draw.text(
        (665, y),
        "최고 체감",
        font=font_small,
        fill=gray
    )

    feels_text = (
        f"{max_feels:.1f}°"
        if max_feels is not None
        else "-"
    )

    draw.text(
        (665, y + 45),
        feels_text,
        font=font_large,
        fill=dark
    )

    # 구분선
    draw.line(
        (
            margin,
            700,
            W - margin,
            700
        ),
        fill=line,
        width=3
    )

    # 강수
    draw.text(
        (margin, 755),
        "강수",
        font=font_small,
        fill=gray
    )

    draw.text(
        (margin, 805),
        f'{weather["max_pop"]}%',
        font=font_large,
        fill=dark
    )

    if weather["precip"]:

        first_time, first_amount = (
            weather["precip"][0]
        )

        rain_text = (
            f"{first_time[:2]}:"
            f"{first_time[2:]}  "
            f"{first_amount}"
        )

    else:
        rain_text = "예상 강수 없음"

    draw.text(
        (350, 820),
        rain_text,
        font=font_medium,
        fill=dark
    )

    # 구분선
    draw.line(
        (
            margin,
            935,
            W - margin,
            935
        ),
        fill=line,
        width=3
    )

    # 미세먼지
    draw.text(
        (margin, 985),
        "미세먼지",
        font=font_small,
        fill=gray
    )

    pm10_value = (
        air["pm10"]
        if air["pm10"] is not None
        else "-"
    )

    draw.text(
        (margin, 1035),
        f"PM10  {pm10_status}  {pm10_value}",
        font=font_medium,
        fill=dark
    )

    pm25_value = (
        air["pm25"]
        if air["pm25"] is not None
        else "-"
    )

    draw.text(
        (margin, 1100),
        f"PM2.5  {pm25_status}  {pm25_value}",
        font=font_medium,
        fill=dark
    )

    # 하단 조언
    advice = make_advice(
        weather,
        air,
        max_feels
    )

    draw.rounded_rectangle(
        (
            margin,
            1190,
            W - margin,
            1285
        ),
        radius=28,
        fill=(230, 230, 225)
    )

    draw.text(
        (margin + 28, 1217),
        advice,
        font=font_small,
        fill=dark
    )

    img.save(
        OUTPUT_FILE,
        quality=95
    )


# =========================================================
# Telegram 사진 전송
# =========================================================

def send_telegram_photo():

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendPhoto"
    )

    with open(
        OUTPUT_FILE,
        "rb"
    ) as image:

        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID
            },
            files={
                "photo": image
            },
            timeout=30
        )

    response.raise_for_status()


# =========================================================
# 실행
# =========================================================

def main():

    items, _, _ = get_weather()

    weather = parse_weather(
        items
    )

    air = get_air_quality()

    create_weather_card(
        weather,
        air
    )

    send_telegram_photo()

    print(
        "Weather card sent successfully."
    )


if __name__ == "__main__":
    main()
