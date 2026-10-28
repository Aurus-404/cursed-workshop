# Supplemental dependency notices

These are license/notice texts, not third-party implementation code. Existing Python package notices are
collected from `.build-deps`, Python's installed LICENSE.txt and three.js's installed LICENSE at release time.
No new runtime dependency was installed for release preparation.

Supplemental original texts obtained for the existing runtime, on 2026-10-15:

- `proxy_tools-LICENSE.txt`: https://raw.githubusercontent.com/jtushman/proxy_tools/master/LICENSE.txt
  (upstream BSD text). Installed proxy_tools 0.1.0 metadata says MIT, but its own source header says BSD;
  preserve the upstream copyright/conditions rather than rewriting them.
- `WebView2-LICENSE.txt`, `WebView2-NOTICE.txt`: from the official NuGet package
  https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/1.0.3856.49
  The installed DLL file version is 1.0.3856.49. Only these notice texts were extracted; no replacement SDK
  binaries were installed or copied into the application.
- `OpenSSL-3.0.16-LICENSE.txt`:
  https://raw.githubusercontent.com/openssl/openssl/openssl-3.0.16/LICENSE.txt
  The bundled Python runtime reports OpenSSL 3.0.16. Python's own notices include its other listed components.

These texts cover dependencies; they do not choose a license for the Cursed Workshop project.
Recheck notice versions if updating runtime packages or rebuilding with a different Python installation.
