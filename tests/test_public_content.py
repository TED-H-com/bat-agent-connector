"""Publication checks use synthetic identities and never echo offending content."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_public_content.py"
SPEC = importlib.util.spec_from_file_location("public_content_guard", SCRIPT)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def test_examples_attribution_and_prose_are_allowed():
    assert GUARD.check_text(
        "/home/operator/project\n/Users/fixture/project\n"
        "Synthetic passwd/home/executable layouts\nCopyright Ted Huang\n"
        "https://github.com/example/project\n"
    ) == []


def test_home_paths_and_conversations_report_categories_only():
    private_user = "sample" + "-owner"
    shared_url = "https://chatgpt.com/" + "share/sample-discussion"
    text = f"/home/{private_user}/project\nC:\\Users\\{private_user}\\project\n{shared_url}"
    assert GUARD.check_text(text) == [
        (1, "non-example home path"),
        (2, "non-example home path"),
        (3, "shared conversation link"),
    ]
    assert GUARD.check_text(f"c:\\users\\{private_user}\\project") == [(1, "non-example home path")]
    assert GUARD.check_text(f"c:/users/{private_user}/project") == [(1, "non-example home path")]


def test_cli_checks_tracked_config_without_disclosing_values(tmp_path):
    git = shutil.which("git")
    subprocess.run([git, "init", "--template=", "-q", str(tmp_path)], check=True)
    secret = "synthetic-value-that-must-not-be-printed"
    (tmp_path / "hosts.toml").write_text(f'token = "{secret}"\n')
    subprocess.run([git, "-C", str(tmp_path), "add", "hosts.toml"], check=True)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "hosts.toml:1: private configuration filename" in result.stdout
    assert secret not in result.stdout + result.stderr


def _gitignore_private_config_patterns() -> list[str]:
    lines = (SCRIPT.parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()
    marker = "# never commit real configs or tokens"
    assert marker in lines
    start = lines.index(marker) + 1
    patterns = []
    for raw in lines[start:]:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            break
        patterns.append(stripped)
    return patterns


def test_gitignore_private_config_block_parity(tmp_path):
    patterns = _gitignore_private_config_patterns()
    assert {"hosts.toml", "*.token", "*.enc.json", ".env"} <= set(patterns)
    git = shutil.which("git")
    subprocess.run([git, "init", "--template=", "-q", str(tmp_path)], check=True)
    expected = []
    for index, pattern in enumerate(patterns):
        sample = f"sample-{index}{pattern[1:]}" if pattern.startswith("*.") else pattern
        assert GUARD.is_private_config_name(sample), pattern
        (tmp_path / sample).write_text("synthetic\n", encoding="utf-8")
        subprocess.run([git, "-C", str(tmp_path), "add", "-f", sample], check=True)
        expected.append((sample, 1, "private configuration filename"))
    findings = GUARD.check_tree(tmp_path)
    for item in expected:
        assert item in findings


def test_private_config_filename_positive_negative_and_exemption(tmp_path):
    git = shutil.which("git")
    subprocess.run([git, "init", "--template=", "-q", str(tmp_path)], check=True)
    flagged = [
        "hosts.toml",
        "discord-channels.toml",
        ".env",
        ".env.local",
        ".env.examplx",
        "service.token",
        "remote-tokens.enc.json",
    ]
    allowed = [
        ".env.example",
        "hosts.example.toml",
        "tunnel.env.example",
        "token-sources.example.toml",
    ]
    for name in flagged + allowed:
        (tmp_path / name).write_text("synthetic\n", encoding="utf-8")
        subprocess.run([git, "-C", str(tmp_path), "add", "-f", name], check=True)
    findings = GUARD.check_tree(tmp_path)
    assert findings == sorted((name, 1, "private configuration filename") for name in flagged)
    for name in GUARD.SYNTHETIC_CONFIG_NAMES:
        assert not GUARD.is_private_config_name(name)
        mutated = name[:-1] + ("x" if name[-1] != "x" else "y")
        assert GUARD.is_private_config_name(mutated)


def test_non_example_home_paths_and_synthetic_user_exemptions():
    for user in GUARD.SYNTHETIC_USERS:
        assert GUARD.check_text(f"/home/{user}/work\n/var/home/{user}/work\nC:\\Users\\{user}\\work") == []
        mutated = user[:-1] + ("z" if user[-1] != "z" else "y")
        assert GUARD.check_text(f"/home/{mutated}/work") == [(1, "non-example home path")]
        assert GUARD.check_text(f"/var/home/{mutated}/work") == [(1, "non-example home path")]
    underscore_user = "_" + "serviceacct"
    digit_user = "9" + "worker"
    var_user = "real" + "person"
    assert GUARD.check_text(f"/home/{underscore_user}/repo") == [(1, "non-example home path")]
    assert GUARD.check_text(f"/home/{digit_user}/repo") == [(1, "non-example home path")]
    assert GUARD.check_text(f"/var/home/{var_user}/repo") == [(1, "non-example home path")]
    assert GUARD.check_text(f"C:\\Users\\{underscore_user}\\repo") == [(1, "non-example home path")]
    assert GUARD.check_text(f"C:/Users/{digit_user}/repo") == [(1, "non-example home path")]


def test_shared_conversation_links_positive_and_negative():
    links = [
        "https://chatgpt.com/" + "share/sample-1",
        "https://chat.openai.com/" + "share/sample-2",
        "https://claude.ai/" + "share/sample-3",
        "https://gemini.google.com/" + "share/sample-4",
        "https://g.co/gemini/" + "share/sample-5",
        "https://grok.com/" + "share/sample-6",
        "https://copilot.microsoft.com/" + "shares/sample-7",
    ]
    for link in links:
        assert GUARD.check_text(link) == [(1, "shared conversation link")], link
    assert GUARD.check_text(
        "https://claude.ai/chats\n"
        "https://gemini.google.com/app\n"
        "https://copilot.microsoft.com/share/not-plural\n"
        "https://github.com/example/project\n"
    ) == []


def test_ssh_key_fingerprints_positive_and_negative():
    sha_fp = "SHA256:" + ("Ab9+/" * 8) + "Z0x"
    md5_fp = ":".join(["a1"] * 16)
    md5_prefixed = "MD5:" + ":".join(["0f"] * 16)
    assert GUARD.check_text(sha_fp) == [(1, "SSH key fingerprint")]
    assert GUARD.check_text(md5_fp) == [(1, "SSH key fingerprint")]
    assert GUARD.check_text(md5_prefixed) == [(1, "SSH key fingerprint")]
    assert GUARD.check_text(
        "SHA256:" + ("A" * 42) + "\n"
        "SHA256:" + ("A" * 44) + "\n"
        + ":".join(["aa"] * 15) + "\n"
        + ":".join(["aa"] * 32) + "\n"
        "aa:bb:cc:dd:ee:ff\n"
    ) == []


def test_connector_tokens_positive_and_negative():
    valid_token = "batc_" + ("A9_-" * 10) + "xyz"
    assert len(valid_token) == 48
    assert GUARD.check_text(valid_token) == [(1, "connector token")]
    cookie_name = "batc_" + ("a1" * 10)
    short_token = "batc_" + ("a" * 42)
    long_token = "batc_" + ("a" * 44)
    prefixed = "x" + valid_token
    assert GUARD.check_text(f"{cookie_name}\n{short_token}\n{long_token}\n{prefixed}\nbatc_pm\n") == []


def test_private_or_tailnet_ipv4_positive_negative_and_exemptions():
    flagged_ips = [
        "10." + "1.2.3",
        "172." + "16.0.1",
        "172." + "31.255.254",
        "192.168." + "1.10",
        "100." + "64.0.9",
        "100." + "127.255.254",
    ]
    for ip in flagged_ips:
        assert GUARD.check_text(f"endpoint = {ip}:22") == [(1, "private or tailnet IPv4 address")], ip
    assert GUARD.check_text(
        "version 1.10." + "1.2.3\n"
        "oid 10.1." + "2.3.4\n"
        "tag v10." + "1.2.3\n"
        "invalid 10." + "999.0.1 and 10." + "01.0.1\n"
        "public 127.0.0.1 192.0.2.1 198.51.100.1 203.0.113.1 172.15.0.1 172.32.0.1 100.63.255.255 100.128.0.1\n"
    ) == []
    for allowed_ip in GUARD.SYNTHETIC_IPV4_ADDRESSES:
        assert GUARD.check_text(allowed_ip) == []
        mutated_ip = allowed_ip[:-1] + ("8" if allowed_ip[-1] != "8" else "7")
        assert GUARD.check_text(mutated_ip) == [(1, "private or tailnet IPv4 address")], mutated_ip


def test_email_addresses_positive_negative_and_exemptions():
    flagged_emails = [
        "dev" + "@" + "company.io",
        "first.last+tag" + "@" + "sub.domain.org",
        "<mailto:person" + "@" + "corp.internal.net>",
        "git" + "@" + "github.com",
        "Write to someone" + "@" + "corp.com.",
        "Write to someone" + "@" + "corp.com. More text.",
    ]
    for email in flagged_emails:
        assert GUARD.check_text(email) == [(1, "e-mail address")], email
    assert GUARD.check_text(
        "alice@example.com\n"
        "name@example.com.\n"
        "bob@example.org\n"
        "carol@example.net\n"
        "user@host.example\n"
        "user@host.invalid\n"
        "user@host.test\n"
        "root@localhost\n"
        "12345+octocat@users.noreply.github.com\n"
        "noreply@anthropic.com\n"
        "https://user" + "@" + "corp.internal.org/repo.git\n"
        "https://user:secret" + "@" + "corp.internal.org/repo.git\n"
        "ssh://git" + "@" + "github.com/owner/repo.git\n"
        "git" + "@" + "github.com:owner/repo.git\n"
        "deploy" + "@" + "prod.internal.org:srv/app.git\n"
    ) == []
    for allowed_email in GUARD.SYNTHETIC_EMAILS:
        assert GUARD.check_text(allowed_email) == []
        local, domain = allowed_email.split("@", 1)
        mutated_local = local[:-1] + ("x" if local[-1] != "x" else "y") + "@" + domain
        mutated_domain = local + "@" + ("x" if domain[0] != "x" else "y") + domain[1:]
        assert GUARD.check_text(mutated_local) == [(1, "e-mail address")]
        assert GUARD.check_text(mutated_domain) == [(1, "e-mail address")]
