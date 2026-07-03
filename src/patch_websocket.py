import os
import ssl
import urllib.parse

def apply_patches():
    proxy_url = os.environ.get("BYBIT_PROXY")
    if proxy_url:
        # Patch python's standard SSL context to disable verification globally
        ssl._create_default_https_context = ssl._create_unverified_context
        ssl.create_default_context = ssl._create_unverified_context
        
        # Force disable SSL verification on ALL contexts
        original_wrap_socket = ssl.SSLContext.wrap_socket
        if getattr(original_wrap_socket, '__name__', '') != 'patched_wrap_socket':
            def patched_wrap_socket(self_ctx, *args, **kwargs):
                self_ctx.check_hostname = False
                self_ctx.verify_mode = ssl.CERT_NONE
                return original_wrap_socket(self_ctx, *args, **kwargs)
            ssl.SSLContext.wrap_socket = patched_wrap_socket
        
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
