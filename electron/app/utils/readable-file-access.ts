import fs from "fs";
import path from "path";
import { getAppDataDir, getTempDir } from "./constants";

export class LocalFileAccessError extends Error {
  constructor(
    message: string,
    public readonly code: "INVALID_PATH" | "NOT_FOUND" | "ACCESS_DENIED",
  ) {
    super(message);
    this.name = "LocalFileAccessError";
  }
}

function resolveBaseDir(baseDir: string): string {
  const resolvedBaseDir = path.resolve(baseDir);
  try {
    return fs.realpathSync(resolvedBaseDir);
  } catch {
    return resolvedBaseDir;
  }
}

function allowedReadableFileBaseDirs(): string[] {
  return [
    ...["images", "uploads", "exports", "pptx-to-html", "pptx-to-json", "fonts", "templates"]
      .map((directory) => path.join(getAppDataDir(), directory)),
    getTempDir(),
  ].map((directory) => path.resolve(directory));
}

function assertPathAllowed(candidatePath: string, baseDirs: string[]): void {
  for (const baseDir of baseDirs) {
    if (candidatePath === baseDir || candidatePath.startsWith(`${baseDir}${path.sep}`)) {
      return;
    }
  }

  throw new LocalFileAccessError(
    "Access denied: File path not allowed",
    "ACCESS_DENIED",
  );
}

export function resolveReadableLocalFile(filePath: unknown): string {
  if (typeof filePath !== "string" || filePath.trim().length === 0) {
    throw new LocalFileAccessError("Invalid file path", "INVALID_PATH");
  }

  const requestedPath = path.resolve(filePath);
  if (path.extname(requestedPath).toLowerCase() !== ".txt") {
    throw new LocalFileAccessError("Access denied: Only document text is readable", "ACCESS_DENIED");
  }
  const allowedBaseDirs = allowedReadableFileBaseDirs();
  assertPathAllowed(requestedPath, [...allowedBaseDirs, ...allowedBaseDirs.map(resolveBaseDir)]);


  let resolvedPath: string;
  try {
    resolvedPath = fs.realpathSync(requestedPath);
  } catch {
    throw new LocalFileAccessError("File not found", "NOT_FOUND");
  }

  assertPathAllowed(resolvedPath, allowedBaseDirs.map(resolveBaseDir));
  if (path.extname(resolvedPath).toLowerCase() !== ".txt") {
    throw new LocalFileAccessError("Access denied: Only document text is readable", "ACCESS_DENIED");
  }
  return resolvedPath;
}

export function readReadableLocalFile(filePath: unknown): string {
  return fs.readFileSync(resolveReadableLocalFile(filePath), "utf-8");
}
