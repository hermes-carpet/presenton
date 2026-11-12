import { fork } from "node:child_process";

export function runNodeScript(scriptPath, scriptArgs, options = {}) {
  return new Promise((resolve, reject) => {
    const child = fork(scriptPath, scriptArgs, {
      cwd: options.cwd,
      stdio: options.stdio || "inherit",
      env: options.env || process.env,
      execArgv: [],
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Node script exited with code: ${code}`));
    });
  });
}
