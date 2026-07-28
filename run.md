  systemctl --user restart reddit-web.service
  systemctl --user restart \
    reddit-crawl.timer \
    reddit-enrich.timer \
    reddit-gemini-backlog.timer