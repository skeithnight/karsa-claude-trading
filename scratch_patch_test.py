import urllib.parse
proxy_url = "socks5h://warp:1080"
parsed = urllib.parse.urlparse(proxy_url)
print("host:", parsed.hostname)
print("port:", parsed.port)
print("scheme:", parsed.scheme)
