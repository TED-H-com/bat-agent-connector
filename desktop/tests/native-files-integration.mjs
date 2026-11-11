// Native adapter + real temporary central; no installed app or live host acceptance is claimed.
import {spawn} from "../../scripts/hidden-child-process.mjs";
import {resolve} from "node:path";
const python = process.env.BATC_NATIVE_TEST_PYTHON || resolve("../.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn("cargo", ["test", "--manifest-path", "src-tauri/Cargo.toml", "native_transfer_real_central", "--", "--ignored", "--nocapture"],
  {stdio: "inherit", env: {...process.env, BATC_NATIVE_TEST_PYTHON: python}});
child.on("error", error => {console.error(error.message); process.exitCode = 1;});
child.on("exit", code => {process.exitCode = code ?? 1;});
