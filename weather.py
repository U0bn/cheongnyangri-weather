import os
import math
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import unquote


# =========================================================
# 환경변수
# =========================================================

KMA_API_KEY = unquote(os.environ["KMA_API_KEY"])
AIRKOREA_API_KEY = unquote(os.environ["AIRKOREA_API_KEY"])

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# =========================================================
# 위치 설정
# =========================================================

# 청량리동 기상청 격자
NX = 61
NY = 127

# 에어코리아 측정소
AIR_STATION = "동대문구"

KST = ZoneInfo("Asia/Seoul")


# =========================================================
# 기상청 발표시각 계산
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

        # API 반영 지연 고려
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
# 기상청 단기예보
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

    items = data[
        "response"
    ][
        "body"
    ][
        "items"
    ][
        "item"
    ]

    return items, base_date, base_time


# =========================================================
# 날씨 데이터 정리
# =========================================================

def parse_weather(items):

    now = datetime.now(KST)

    today = now.strftime("%Y%m%d")

    hourly = {}

    for item in items:

        fcst_date = item["fcstDate"]

        if fcst_date != today:
            continue

        fcst_time = item["fcstTime"]
        category = item["category"]
        value = item["fcstValue"]

        if fcst_time not in hourly:
            hourly[fcst_time] = {}

        hourly[fcst_time][category] = value

    if not hourly:
        raise Exception("오늘 예보 데이터가 없습니다.")

    times = sorted(hourly.keys())

    current_hour = now.strftime("%H00")

    future_times = [
        t for t in times
        if t >= current_hour
    ]

    if future_times:
        current_time = future_times[0]
    else:
        current_time = times[-1]

    current = hourly[current_time]

    current_temp = current.get(
        "TMP",
        "?"
    )

    temps = []
    pops = []
    precip = []

    for t in times:

        values = hourly[t]

        # 기온
        if "TMP" in values:

            try:
                temps.append(
                    float(values["TMP"])
                )

            except ValueError:
                pass

        # 강수확률
        if "POP" in values:

            try:
                pops.append(
                    int(values["POP"])
                )

            except ValueError:
                pass

        # 강수량
        if "PCP" in values:

            pcp = values["PCP"]

            if pcp not in [
                "강수없음",
                "0",
                "0.0"
            ]:

                precip.append(
                    (t, pcp)
                )

    min_temp = (
        min(temps)
        if temps
        else "?"
    )

    max_temp = (
        max(temps)
        if temps
        else "?"
    )

    max_pop = (
        max(pops)
        if pops
        else 0
    )

    return {
        "current_time": current_time,
        "current_temp": current_temp,
        "min_temp": min_temp,
        "max_temp": max_temp,
        "max_pop": max_pop,
        "precip": precip,
        "hourly": hourly
    }


# =========================================================
# 체감온도 계산
# =========================================================

def wet_bulb_temperature(temp, rh):

    return (
        temp
        * math.atan(
            0.151977
            * math.sqrt(
                rh + 8.313659
            )
        )

        + math.atan(
            temp + rh
        )

        - math.atan(
            rh - 1.67633
        )

        + 0.00391838
        * (rh ** 1.5)
        * math.atan(
            0.023101 * rh
        )

        - 4.686035
    )


def apparent_temperature(temp, rh):

    tw = wet_bulb_temperature(
        temp,
        rh
    )

    feels = (
        -0.2442
        + 0.55399 * tw
        + 0.45535 * temp
        - 0.0022 * (tw ** 2)
        + 0.00278 * tw * temp
        + 3.0
    )

    return feels


def get_max_apparent_temperature(hourly):

    values = []

    for time, data in hourly.items():

        if (
            "TMP" not in data
            or "REH" not in data
        ):
            continue

        try:

            temp = float(
                data["TMP"]
            )

            rh = float(
                data["REH"]
            )

        except ValueError:
            continue

        feels = apparent_temperature(
            temp,
            rh
        )

        values.append(feels)

    if not values:
        return None

    return max(values)


# =========================================================
# 에어코리아 미세먼지
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
        "ver": "1.3"
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    items = data.get(
        "response",
        {}
    ).get(
        "body",
        {}
    ).get(
        "items",
        []
    )

    if not items:

        return {
            "pm10": None,
            "pm25": None,
            "data_time": "정보없음"
        }

    # 최신 측정자료
    item = items[0]

    pm10 = item.get(
        "pm10Value"
    )

    pm25 = item.get(
        "pm25Value"
    )

    data_time = item.get(
        "dataTime",
        "정보없음"
    )

    # "-" 값 처리
    if pm10 in [
        None,
        "",
        "-"
    ]:
        pm10 = None

    if pm25 in [
        None,
        "",
        "-"
    ]:
        pm25 = None

    return {
        "pm10": pm10,
        "pm25": pm25,
        "data_time": data_time
    }


# =========================================================
# 미세먼지 등급
# =========================================================

def pm10_grade(value):

    if value is None:
        return "정보없음"

    try:
        value = int(value)

    except ValueError:
        return "정보없음"

    if value <= 30:
        return "좋음"

    elif value <= 80:
        return "보통"

    elif value <= 150:
        return "나쁨"

    else:
        return "매우 나쁨"


def pm25_grade(value):

    if value is None:
        return "정보없음"

    try:
        value = int(value)

    except ValueError:
        return "정보없음"

    if value <= 15:
        return "좋음"

    elif value <= 35:
        return "보통"

    elif value <= 75:
        return "나쁨"

    else:
        return "매우 나쁨"


# =========================================================
# 텔레그램 메시지 생성
# =========================================================

def make_message(
    weather,
    air,
    base_date,
    base_time
):

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

    precip_bad = (
        len(weather["precip"]) > 0
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

    # 오늘 꼭 확인 여부
    if (
        precip_bad
        or pm10_bad
        or pm25_bad
        or heat_bad
    ):

        status = "🚨 오늘 꼭 확인"

    else:

        status = "✅ 외출 무난"

    lines = [
        status,
        "",
        "서울 동대문구 청량리동",
        "",
        f'현재 기온: '
        f'{weather["current_temp"]}℃',

        f'오늘 최저: '
        f'{weather["min_temp"]}℃',

        f'오늘 최고: '
        f'{weather["max_temp"]}℃',

        f'오늘 최대 강수확률: '
        f'{weather["max_pop"]}%'
    ]

    # 체감온도
    if max_feels is not None:

        lines.append(
            f'오늘 최고 체감온도: '
            f'{max_feels:.1f}℃'
        )

    # 강수
    if weather["precip"]:

        lines.append("")
        lines.append("강수 예보:")

        for time, amount in weather["precip"]:

            formatted = (
                f"{time[:2]}:"
                f"{time[2:]}"
            )

            lines.append(
                f"- {formatted} / {amount}"
            )

    else:

        lines.append(
            "예상 강수량: 강수 없음"
        )

    # 미세먼지
    lines.append("")

    pm10_value = (
        air["pm10"]
        if air["pm10"] is not None
        else "정보없음"
    )

    pm25_value = (
        air["pm25"]
        if air["pm25"] is not None
        else "정보없음"
    )

    lines.append(
        f"미세먼지 PM10: "
        f"{pm10_status} / "
        f"{pm10_value}"
        + (
            "㎍/㎥"
            if air["pm10"] is not None
            else ""
        )
    )

    lines.append(
        f"초미세먼지 PM2.5: "
        f"{pm25_status} / "
        f"{pm25_value}"
        + (
            "㎍/㎥"
            if air["pm25"] is not None
            else ""
        )
    )

    lines.extend([
        "",
        f'에어코리아 측정: '
        f'{air["data_time"]}',

        f'기상청 발표: '
        f'{base_date} {base_time}'
    ])

    return "\n".join(lines)


# =========================================================
# 텔레그램 전송
# =========================================================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()


# =========================================================
# 실행
# =========================================================

def main():

    items, base_date, base_time = (
        get_weather()
    )

    weather = parse_weather(
        items
    )

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
