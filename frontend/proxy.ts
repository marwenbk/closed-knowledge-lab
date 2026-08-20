import { type NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  const headers = new Headers(request.headers);
  const publicOrigin = process.env.TOPMED_PUBLIC_ORIGIN;
  if (!headers.has("origin") && publicOrigin) {
    headers.set("origin", publicOrigin);
  }
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: ["/api/:path*"],
};
