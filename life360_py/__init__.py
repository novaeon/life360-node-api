"""Python implementation of the life360-node-api library.

This module mirrors the public behavior and object model of the Node.js
implementation as closely as possible while following Python conventions.
"""

from __future__ import annotations

import base64
import json
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import urlencode

import requests


DEBUG_FLAG = False


def _try_create_float(value: Any) -> Any:
    if isinstance(value, str):
        if re.match(r"^-?\d+(?:[.,]\d*?)?$", value):
            try:
                return float(value)
            except ValueError:
                return value
    return value


def _try_create_int(value: Any) -> Any:
    if isinstance(value, str) and value.isdigit():
        try:
            return int(value)
        except ValueError:
            return value
    return value


def _try_create_bool(value: Any) -> Any:
    if value in ("1", 1, "yes", "true", True):
        return True
    if value in ("0", 0, "no", "false", False):
        return False
    return value


def _try_create_date(value: Any) -> Any:
    if value is None:
        return value
    value = _try_create_int(value)
    if isinstance(value, (int, float)) and value < 99_999_999_999:
        value = value * 1000
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return value
    except Exception:
        return value


def _find_lat_lon(value: Any) -> Dict[str, Any]:
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return _find_lat_lon(value[0])
        if len(value) != 2:
            raise ValueError("Unable to parse coordinates")
        a, b = value
        if a > 90 or a < -90:
            return {"lat": b, "lon": a}
        return {"lat": a, "lon": b}
    if isinstance(value, dict):
        lat = value.get("lat", value.get("latitude", value.get("y")))
        lon = value.get(
            "lon",
            value.get("longitude", value.get("lng", value.get("long", value.get("x")))),
        )
        if lat is None:
            raise ValueError("Unable to find latitude from coordinates")
        if lon is None:
            raise ValueError("Unable to find longitude from coordinates")
        return {"lat": lat, "lon": lon}
    raise ValueError("Unable to parse coordinates")


class Life360Helper:
    def __init__(self, api: "Life360"):
        if not isinstance(api, Life360):
            raise TypeError("api must be a Life360 instance")
        self.api = api

    def request(self, *args: Any, **kwargs: Any) -> Any:
        return self.api.request(*args, **kwargs)


class Life360List(Life360Helper):
    def __init__(self, api: "Life360"):
        super().__init__(api)
        self._items: List[Any] = []

    def __iter__(self) -> Iterator[Any]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> Any:
        return self._items[index]

    def clear_children(self) -> None:
        self._items.clear()

    def add_child(self, child: Any) -> Any:
        self._items.append(child)
        return child


class Life360Location(Life360Helper):
    def populate(self, payload: Dict[str, Any]) -> None:
        self.__dict__.update(payload)
        for key in ("startTimestamp", "endTimestamp", "since", "timestamp"):
            if key in payload:
                setattr(self, key, _try_create_date(payload[key]))
        for key in ("accuracy", "battery", "charge", "speed"):
            if key in payload:
                setattr(self, key, _try_create_int(payload[key]))
        for key in ("inTransit", "isDriving", "wifiState"):
            if key in payload:
                setattr(self, key, _try_create_bool(payload[key]))


class Life360LocationList(Life360List):
    pass


class Life360Request(Life360Helper):
    def populate(self, payload: Dict[str, Any]) -> None:
        self.__dict__.update(payload)
        self.requestId = payload.get("requestId")
        self.isPollable = payload.get("isPollable")


class Life360LocationRequest(Life360Request):
    def check(self) -> bool:
        payload = self.request(f"/v3/circles/members/request/{self.requestId}")
        if payload.get("status") == "A":
            location = Life360Location(self.api)
            location.populate(payload["location"])
            self.location = location
            self.success_response = payload
            return True
        return False


class Life360CheckInRequest(Life360Request):
    def check(self) -> bool:
        payload = self.request(f"/v3/circles/members/request/{self.requestId}")
        if payload.get("status") == "A":
            self.location = payload.get("location")
            self.success_response = payload
            return True
        return False


class Life360Member(Life360Helper):
    def populate(self, payload: Dict[str, Any]) -> None:
        self.__dict__.update(payload)
        for key in ("created", "createdAt"):
            if key in payload:
                setattr(self, key, _try_create_date(payload[key]))
        if "isAdmin" in payload:
            self.isAdmin = _try_create_bool(payload["isAdmin"])
        if payload.get("location"):
            location = Life360Location(self.api)
            location.populate(payload["location"])
            self.location = location

    def refresh(self) -> "Life360Member":
        payload = self.api.member(self.circle.id, self.id)
        self.populate(payload)
        return self

    def history(self, at_time: Optional[Any] = None) -> Life360LocationList:
        params = None
        if at_time is not None:
            if isinstance(at_time, datetime):
                at_time = int(at_time.timestamp())
            elif isinstance(at_time, str):
                at_time = int(datetime.fromisoformat(at_time.replace("Z", "+00:00")).timestamp())
            params = {"time": at_time}
        payload = self.request(
            f"/v3/circles/{self.circle.id}/members/{self.id}/history", {"params": params}
        )
        out = Life360LocationList(self.api)
        for item in payload.get("locations", []):
            location = Life360Location(self.api)
            location.populate(item)
            out.add_child(location)
        return out

    def request_location(self) -> Life360LocationRequest:
        payload = self.request(
            f"/v3/circles/{self.circle.id}/members/{self.id}/request",
            {"method": "post", "body": {"type": "location"}},
        )
        out = Life360LocationRequest(self.api)
        out.populate(payload)
        out.member = self
        out.circle = self.circle
        return out

    def request_checkin(self) -> Life360CheckInRequest:
        payload = self.request(
            f"/v3/circles/{self.circle.id}/members/{self.id}/request",
            {"method": "post", "body": {"type": "checkin"}},
        )
        out = Life360CheckInRequest(self.api)
        out.populate(payload)
        out.member = self
        out.circle = self.circle
        return out


class Life360MemberList(Life360List):
    def populate(self, payload: Iterable[Dict[str, Any]]) -> None:
        for item in payload:
            member = Life360Member(self.api)
            member.populate(item)
            member.circle = self.circle
            self.add_child(member)

    def find_by_id(self, member_id: str) -> Optional[Life360Member]:
        for member in self:
            if member.id == member_id:
                return member
        return None

    def find_by_name(self, name: str) -> Optional[Life360Member]:
        regex = re.compile(f".*{re.escape(name)}.*", re.IGNORECASE)
        for member in self:
            full_name = f"{getattr(member, 'firstName', '')} {getattr(member, 'lastName', '')}".strip()
            if regex.match(getattr(member, "firstName", "")) or regex.match(
                getattr(member, "lastName", "")
            ) or regex.match(full_name):
                return member
        return None


class Life360Circle(Life360Helper):
    def populate(self, payload: Dict[str, Any]) -> None:
        self.__dict__.update(payload)
        for key in ("createdAt", "memberCount", "unreadMessages", "unreadNotifications"):
            if key in payload:
                converter = _try_create_date if key == "createdAt" else _try_create_int
                setattr(self, key, converter(payload[key]))

        self.members = Life360MemberList(self.api)
        self.members.circle = self
        if payload.get("members"):
            self.members.populate(payload["members"])

    def refresh(self) -> "Life360Circle":
        payload = self.request(f"/v3/circles/{self.id}")
        self.populate(payload)
        return self

    def list_members(self) -> Life360MemberList:
        payload = self.request(f"/v3/circles/{self.id}/members")
        members = Life360MemberList(self.api)
        members.circle = self
        members.populate(payload.get("members", []))
        self.members = members
        return members

    def all_places(self) -> Dict[str, Any]:
        return self.request(f"/v3/circles/{self.id}/allplaces")


class Life360CircleList(Life360List):
    def populate(self, payload: Dict[str, Any]) -> None:
        for item in payload.get("circles", []):
            circle = Life360Circle(self.api)
            circle.populate(item)
            self.add_child(circle)

    def find_by_id(self, circle_id: str) -> Optional[Life360Circle]:
        for circle in self:
            if circle.id == circle_id:
                return circle
        return None

    def find_by_name(self, name: str) -> Optional[Life360Circle]:
        regex = re.compile(f".*{re.escape(name)}.*", re.IGNORECASE)
        for circle in self:
            if regex.match(getattr(circle, "name", "")):
                return circle
        return None


class Life360Session(Life360Helper):
    def populate(self, payload: Dict[str, Any]) -> None:
        self.__dict__.update(payload)
        self.token_type = payload.get("token_type")
        self.access_token = payload.get("access_token")


class Life360:
    BASIC_AUTH = (
        "Basic "
        "U3dlcUFOQWdFVkVoVWt1cGVjcmVrYXN0ZXFhVGVXckFTV2E1dXN3MzpXMnZBV3JlY2hhUHJl"
        "ZGFoVVJhZ1VYYWZyQW5hbWVqdQ=="
    )

    def __init__(self) -> None:
        self.defaults = {
            "hostname": "www.life360.com",
            "headers": {
                "Accept": "application/json",
                "X-Application": "life360-web-client",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/78.0.3904.108 Safari/537.36"
                ),
            },
        }
        self._device_id: Optional[str] = None
        self.session: Optional[Life360Session] = None

    @classmethod
    def login(cls, *args: Any, **kwargs: Any) -> "Life360":
        instance = cls()
        return instance._login(*args, **kwargs)

    def enable_debugging(self) -> None:
        global DEBUG_FLAG
        DEBUG_FLAG = True

    def disable_debugging(self) -> None:
        global DEBUG_FLAG
        DEBUG_FLAG = False

    def _get_device_id(self) -> str:
        if self._device_id is None:
            self._device_id = "".join(f"{random.getrandbits(8):02x}" for _ in range(8))
        return self._device_id

    def _login(self, *args: Any) -> "Life360":
        body = {
            "countryCode": 1,
            "password": "",
            "username": "",
            "phone": "",
            "grant_type": "password",
        }
        if len(args) == 0:
            raise ValueError("Must provide an argument to Life360.login")
        if len(args) == 1 and isinstance(args[0], dict):
            arg = args[0]
            body["username"] = arg.get("username") or arg.get("user") or arg.get("email") or ""
            body["phone"] = arg.get("phone") or ""
            body["password"] = arg.get("password") or arg.get("pass") or ""
        elif len(args) == 2 and all(isinstance(x, str) for x in args):
            first, password = args
            if re.match(r"^[^@]+@[^\.]+$", first):
                body["username"] = first
            elif re.match(r"^[0-9()\-+ #\.]+$", first):
                body["phone"] = first
            else:
                body["username"] = first
            body["password"] = password
        else:
            raise ValueError("Invalid login arguments")

        payload = self.request(
            "/v3/oauth2/token",
            {
                "authorization": self.BASIC_AUTH,
                "body": body,
                "headers": {"X-Device-Id": self._get_device_id()},
            },
        )
        if not payload.get("token_type"):
            payload["token_type"] = "Bearer"
        self.session = Life360Session(self)
        self.session.populate(payload)
        return self

    def me(self) -> Life360Member:
        payload = self.request("/v3/users/me")
        me = Life360Member(self)
        me.populate(payload)
        self._me = me
        return me

    def list_circles(self) -> Life360CircleList:
        payload = self.request("/v3/circles")
        circles = Life360CircleList(self)
        circles.populate(payload)
        self._circles = circles
        return circles

    def member(self, circle_id: str, member_id: str) -> Dict[str, Any]:
        return self.request(f"/v3/circles/{circle_id}/members/{member_id}")

    def list_crimes(self, args: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        args = args or {}
        if args.get("start"):
            params["startDate"] = args["start"]
        if args.get("end"):
            params["endDate"] = args["end"]
        if args.get("topLeft"):
            top = _find_lat_lon(args["topLeft"])
            params["boundingBox[topLeftLatitude]"] = top["lat"]
            params["boundingBox[topLeftLongitude]"] = top["lon"]
        if args.get("bottomRight"):
            bot = _find_lat_lon(args["bottomRight"])
            params["boundingBox[bottomRightLatitude]"] = bot["lat"]
            params["boundingBox[bottomRightLongitude]"] = bot["lon"]
        payload = self.request("/v3/crimes", {"params": params})
        return payload.get("crimes", [])

    def put_location(self, data: Dict[str, Any]) -> Dict[str, Any]:
        geolocation = {k: data[k] for k in ("lat", "lon", "alt", "accuracy", "heading", "speed", "timestamp", "age") if k in data}
        geolocation_meta = {k: data[k] for k in ("wssid", "reqssid", "lmode") if k in data}
        device = {
            "battery": data.get("battery"),
            "charge": data.get("charge"),
            "wifi_state": data.get("wifiState"),
            "build": data.get("build", "228980"),
            "driveSDKStatus": data.get("driveSDKStatus", "OFF"),
            "userActivity": data.get("userActivity", "unknown"),
        }
        geolocation.setdefault("alt", "0.0")
        geolocation.setdefault("accuracy", "10.00")
        geolocation.setdefault("heading", "0.0")
        geolocation.setdefault("speed", "0.0")
        geolocation.setdefault("timestamp", str(int(time.time())))
        for key in ("lat", "lon"):
            if key in geolocation and isinstance(geolocation[key], (int, float)):
                geolocation[key] = str(geolocation[key])

        user_context = {
            "geolocation": geolocation,
            "geolocation_meta": geolocation_meta,
            "device": {k: v for k, v in device.items() if v is not None},
        }
        encoded = base64.b64encode(json.dumps(user_context).encode("utf-8")).decode("utf-8")
        return self.request(
            "/v4/locations",
            {
                "hostname": "android.life360.com",
                "method": "put",
                "headers": {"X-Device-ID": self._get_device_id(), "X-UserContext": encoded},
            },
        )

    def request(self, path: str, options: Optional[Dict[str, Any]] = None) -> Any:
        options = options or {}
        hostname = options.get("hostname", self.defaults["hostname"])
        headers = dict(self.defaults.get("headers", {}))
        headers.update(options.get("headers", {}))

        method = options.get("method")
        body = options.get("body")
        if not method:
            method = "POST" if body is not None else "GET"
        method = method.upper()

        authorization = options.get("auth") or options.get("authorization")
        if not authorization and self.session:
            authorization = f"{self.session.token_type} {self.session.access_token}"
        if isinstance(authorization, str) and authorization:
            if " " not in authorization:
                authorization = f"Basic {authorization}"
            headers["Authorization"] = authorization

        data = None
        if body is not None:
            body_type = options.get("type", "form-urlencoded")
            if body_type == "json":
                data = json.dumps(body)
                headers["Content-Type"] = "application/json"
            elif body_type == "form-urlencoded":
                data = urlencode(body)
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = body if isinstance(body, str) else json.dumps(body)
                headers["Content-Type"] = body_type if "/" in body_type else f"application/{body_type}"

        params = options.get("params")
        url = f"https://{hostname}{path if path.startswith('/') else '/' + path}"
        response = requests.request(method, url, headers=headers, params=params, data=data, timeout=30)

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        parsed: Any = response.text
        if content_type == "application/json":
            parsed = response.json()

        if not DEBUG_FLAG and isinstance(parsed, dict) and parsed.get("errorMessage") is not None:
            raise RuntimeError(f"API responded with {parsed['errorMessage']}")
        if response.status_code != 200:
            raise RuntimeError(f"Server responded with {response.status_code}, {response.reason}")
        return parsed


__all__ = ["Life360"]
