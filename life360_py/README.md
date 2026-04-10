# life360_py

A Python port of the original `life360-node-api` library.

## Install

```bash
pip install requests
```

Then import from this repository checkout:

```python
from life360_py import Life360
```

## Usage

```python
from life360_py import Life360

client = Life360.login({"email": "myuser@example.com", "password": "mySecurePassword123"})
circles = client.list_circles()

for circle in circles:
    print(circle.name)
    for member in circle.list_members():
        print(member.firstName, member.lastName)
```

## Notes

- The Python API mirrors the Node object graph: circles, members, locations, and request polling objects.
- Networking is synchronous via `requests`.
