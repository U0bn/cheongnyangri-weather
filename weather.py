import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import unquote

KMA_API_KEY = unquote(os.environ["KMA_API_KEY"])
AIRKOREA_API_KEY = unquote(os.environ["AIRKOREA_API_KEY"])

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# 청량리동 기상청 격자
NX = 61
NY = 127

# 우선 동대문구 측정소 사용
AIR_STATION = "동대문구"

KST = ZoneInfo("Asia/Seoul")


def get_base_datetime():
    now = datetime.now(KST)

    base_times = [
        "0200", "0500", "0800", "1100",
        "1400", "1700", "2000", "2300"
    ]

    available = []

    for bt in base_times:
        h = int(bt[:2])
        dt = now.replace(hour=h, minute=0, second=0, microsecond=0)

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

    return base_dt.strftime("%Y%m%d"), base_dt.strftime("%H%M")


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

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()

    data = response.json()

    header = data["response"]["header"]

    if header["resultCode"] != "00":
        raise Exception(
            f'기상청 API 오류: {header["resultCode"]} '
            f'{header["resultMsg"]}'
        )

    items = data["response"]["body"]["items"]["item"]

    return items, base_date, base_time


def parse_weather(items):
    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")

    hourly = {}

    tmn = None
    tmx = None

    for item in items:
        if item["fcstDate"] != today:
            continue

        fcst_time = item["fcstTime"]
        category = item["category"]
        value = item["fcstValue"]

        if category == "TMN":
            try:
                tmn = float(value)
            except ValueError:
                pass

        if category == "TMX":
            try:
                tmx = float(value)
            except ValueError:
                pass

        if fcst_time not in hourly:
            hourly[fcst_time] = {}

        hourly[fcst_time][category] = value

    if not hourly:
        raise Exception("오늘 예보 데이터가 없습니다.")

    times = sorted(hourly.keys())

    current_hour = now.strftime("%H00")
    future_times = [t for t in times if t >= current_hour]

    if future_times:
        current_time = future_times[0]
    else:
        current_time = times[-1]

    current = hourly[current_time]

    current_temp = current.get("TMP", "?")

    temps = []
    pops = []
    precip = []

    for t in times:
        values = hourly[t]

        if "TMP" in values:
            try:
                temps.append(float(values["TMP"]))
            except ValueError:
                pass

        if "POP" in values:
            try:
                pops.append(int(values["POP"]))
            except ValueError:
                pass

        if "PCP" in values:
            pcp = values["PCP"]

            if pcp not in ["강수없음", "0", "0.0"]:
                precip.append((t, pcp))

    if tmn is None:
        tmn = min(temps) if temps else "?"

    if tmx is None:
        tmx = max(temps) if temps else "?"

    return {
        "current_time": current_time,
        "current_temp": current_temp,
        "min_temp": tmn,
        "max_temp": tmx,
        "max_pop": max(pops) if pops else 0,
        "precip": precip,
        "hourly": hourly
    }


def get_air_quality():
    url = (
        "https://apis.data.go.kr/B552584/"
        "ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
    )

    params = {
        "serviceKey": AIRKOREA_API_KEY,
        "returnType": "json",
        "numOfRows": "1",
        "pageNo": "1",
        "stationName": AIR_STATION,
        "dataTerm": "DAILY",
        "ver": "1.4"
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()

    data = response.json()

    items = data.get("response", {}).get("body", {}).get("items", [])

    if not items:
        raise Exception("에어코리아 측정값이 없습니다.")

    item = items[0]

    def parse_value(value):
        if value in [None, "-", ""]:
            return None

        try:
            return float(value)
        except ValueError:
            return None

    return {
        "pm10": parse_value(item.get("pm10Value")),
        "pm25": parse_value(item.get("pm25Value")),
        "time": item.get("dataTime", "?")
    }


def pm10_grade(value):
    if value is None:
        return "정보없음"

    if value <= 30:
        return "좋음"
    elif value <= 80:
        return "보통"
    elif value <= 150:
        return "나쁨"
    else:
        return "매우나쁨"


def pm25_grade(value):
    if value is None:
        return "정보없음"

    if value <= 15:
        return "좋음"
    elif value <= 35:
        return "보통"
    elif value <= 75:
        return "나쁨"
    else:
        return "매우나쁨"


def make_message(weather, air, base_date, base_time):
    precip_exists = len(weather["precip"]) > 0

    pm10_bad = air["pm10"] is not None and air["pm10"] > 80
    pm25_bad = air["pm25"] is not None and air["pm25"] > 35

    if precip_exists or pm10_bad or pm25_bad:
        status = "🚨 오늘 꼭 확인"
    else:
        status = "✅ 외출 무난"

    pm10_value = (
        f'{air["pm10"]:.0f}㎍/㎥'
        if air["pm10"] is not None
        else "정보없음"
    )

    pm25_value = (
        f'{air["pm25"]:.0f}㎍/㎥'
        if air["pm25"] is not None
        else "정보없음"
    )

    lines = [
        status,
        "",
        "서울 동대문구 청량리동",
        "",
        f'현재 기온: {weather["current_temp"]}℃',
        f'오늘 최저: {weather["min_temp"]}℃',
        f'오늘 최고: {weather["max_temp"]}℃',
        f'오늘 최대 강수확률: {weather["max_pop"]}%',
        ""
    ]

    if weather["precip"]:
        lines.append("강수 예보:")

        for time, amount in weather["precip"]:
            formatted = f"{time[:2]}:{time[2:]}"
            lines.append(f"- {formatted} / {amount}")
    else:
        lines.append("예상 강수량: 강수 없음")

    lines.extend([
        "",
        f'미세먼지 PM10: {pm10_grade(air["pm10"])} / {pm10_value}',
        f'초미세먼지 PM2.5: {pm25_grade(air["pm25"])} / {pm25_value}',
        "",
        f'에어코리아 측정: {air["time"]}',
        f"기상청 발표: {base_date} {base_time}"
    ])

    return "\n".join(lines)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()


def main():
    items, base_date, base_time = get_weather()
    weather = parse_weather(items)

    air = get_air_quality()

    message = make_message(
        weather,
        air,
        base_date,
        base_time
    )

    print(message)

    send_telegram(message)


if __name__ == "__main__":
    main()
