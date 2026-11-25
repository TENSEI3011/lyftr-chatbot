#!/bin/sh
# Write runtime config so the same image can point at any backend via API_URL.
if [ -n "$API_URL" ]; then
  echo "window.__APP_CONFIG__ = { API_URL: \"$API_URL\" };" > /usr/share/nginx/html/config.js
fi
