import { NextRequest, NextResponse } from "next/server";

/**
 * Image proxy to bypass CORS restrictions on ar5iv / arxiv figures.
 * Usage: /api/image-proxy?url=https://ar5iv.org/...
 */
export async function GET(req: NextRequest) {
  const url = req.nextUrl.searchParams.get("url");
  if (!url) {
    return new NextResponse("Missing url parameter", { status: 400 });
  }

  // Only allow http/https URLs
  let parsed: URL;
  try {
    parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new Error("Invalid protocol");
    }
  } catch {
    return new NextResponse("Invalid URL", { status: 400 });
  }

  try {
    const referer = `${parsed.protocol}//${parsed.host}/`;
    const upstream = await fetch(parsed.toString(), {
      headers: {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/webp,image/avif,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
      },
      redirect: "follow",
      signal: AbortSignal.timeout(15_000),
    });

    if (!upstream.ok) {
      return new NextResponse(`Upstream error: ${upstream.status}`, {
        status: upstream.status,
      });
    }

    const contentType = upstream.headers.get("content-type") || "image/jpeg";
    const buffer = await upstream.arrayBuffer();

    return new NextResponse(buffer, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
        "X-Proxied-From": parsed.hostname,
      },
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : "Unknown";
    return new NextResponse(`Proxy error: ${msg}`, {
      status: 502,
    });
  }
}
