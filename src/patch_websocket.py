import os
import ssl
import urllib.parse

def apply_patches():
    proxy_url = os.environ.get("BYBIT_PROXY")
    if proxy_url:
        # Security: Global SSL verification MUST NOT be disabled.
        # Removed vulnerable monkey patch that set ssl._create_unverified_context.
        
        # We also need to force the SOCKS5 proxy for websocket-client
        try:
            import websocket
            parsed = urllib.parse.urlparse(proxy_url)
            original_run_forever = websocket.WebSocketApp.run_forever
            if getattr(original_run_forever, '__name__', '') != 'patched_run_forever':
                def patched_run_forever(self_ws, *args, **kwargs):
                    if parsed.hostname:
                        kwargs["http_proxy_host"] = parsed.hostname
                    if parsed.port:
                        kwargs["http_proxy_port"] = parsed.port
                    if parsed.scheme:
                        kwargs["proxy_type"] = parsed.scheme
                    print(f"MONKEY PATCH EXECUTED FOR WEBSOCKET! kwargs={kwargs}", flush=True)
                    return original_run_forever(self_ws, *args, **kwargs)
                websocket.WebSocketApp.run_forever = patched_run_forever
        except Exception as e:
            print("MONKEY PATCH ERROR:", e, flush=True)

apply_patches()
