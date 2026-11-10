from bat_agent_connector import api_v1


def test_host_is_loopback():
    assert api_v1._host_is_loopback("localhost")
    assert api_v1._host_is_loopback("localhost:8080")
    assert api_v1._host_is_loopback("127.0.0.1")
    assert api_v1._host_is_loopback("127.0.0.1:9090")
    assert api_v1._host_is_loopback("[::1]")
    assert api_v1._host_is_loopback("[::1]:80")

    assert not api_v1._host_is_loopback("example.com")
    assert not api_v1._host_is_loopback("192.168.1.1")
    assert not api_v1._host_is_loopback("localhost.localdomain")
    assert not api_v1._host_is_loopback("localhost:3000.evil.com")
    assert not api_v1._host_is_loopback("127.0.0.1:9090.evil.com")
    assert not api_v1._host_is_loopback("localhost:")


def test_origin_ok():
    allowed = ("http://localhost:3000", "https://example.com")
    assert api_v1._origin_ok("http://localhost:3000", allowed)
    assert api_v1._origin_ok("https://example.com", allowed)

    assert not api_v1._origin_ok("https://attacker.com", allowed)
    assert not api_v1._origin_ok("http://localhost:3000.evil.com", allowed)


def test_is_dashboard_path():
    assert api_v1.is_dashboard_path("/")
    assert api_v1.is_dashboard_path("/dashboard")
    assert api_v1.is_dashboard_path("/dashboard/something")
    assert api_v1.is_dashboard_path("/dashboard/")

    assert not api_v1.is_dashboard_path("/api/v1/sessions")
    assert not api_v1.is_dashboard_path("/api/v2/something")
    assert not api_v1.is_dashboard_path("/api/")
    assert not api_v1.is_dashboard_path("/index.html")
