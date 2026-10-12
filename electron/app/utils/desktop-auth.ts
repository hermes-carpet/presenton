import { randomBytes } from "crypto";
import type { Session } from "electron";

export const DESKTOP_COOKIE_NAME = "presenton_desktop_session";
export const DESKTOP_TOKEN_HEADER = "x-presenton-desktop-token";

export function desktopCookieHeader(): string | undefined {
  const token = process.env.PRESENTON_DESKTOP_TOKEN;
  return token ? `${DESKTOP_COOKIE_NAME}=${token}` : undefined;
}

export async function initializeDesktopSession(
  session: Session,
  origins: string[],
): Promise<void> {
  const token = randomBytes(32).toString("hex");
  // Never persist this credential in userConfig or expose it through preload.
  process.env.PRESENTON_DESKTOP_TOKEN = token;
  process.env.PRESENTON_DESKTOP_ORIGINS = origins.join(",");
  await session.cookies.set({
    url: origins[0],
    name: DESKTOP_COOKIE_NAME,
    value: token,
    httpOnly: true,
    sameSite: "strict",
    path: "/",
  });
  // Cross-port renderer fetches often omit credentials. Attach the capability
  // in the main process, only to the two exact application origins.
  session.webRequest.onBeforeSendHeaders((details, callback) => {
    let isAppRequest = false;
    try {
      isAppRequest = origins.includes(new URL(details.url).origin);
    } catch { /* Non-HTTP requests do not receive the token. */ }
    const requestHeaders = { ...details.requestHeaders };
    for (const key of Object.keys(requestHeaders)) {
      if (key.toLowerCase() === DESKTOP_TOKEN_HEADER) delete requestHeaders[key];
      // Cookies are scoped by hostname, not port. Do not leak this cookie to
      // another loopback service or carry it across a redirect off the app.
      if (!isAppRequest && key.toLowerCase() === "cookie") {
        const cookies = String(requestHeaders[key]).split(";")
          .filter((part) => !part.trim().startsWith(`${DESKTOP_COOKIE_NAME}=`));
        if (cookies.some((part) => part.trim())) requestHeaders[key] = cookies.join(";");
        else delete requestHeaders[key];
      }
    }
    if (isAppRequest) requestHeaders[DESKTOP_TOKEN_HEADER] = token;
    callback({ requestHeaders });
  });
}
