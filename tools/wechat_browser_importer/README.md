# WeChat Browser Importer

Local-only fallback for accounts that cannot use the official WeChat MP API.

It opens `https://mp.weixin.qq.com/`, waits for you to scan and log in, then tries to open the draft editor and paste the newest generated article package. It does not save passwords, tokens, or cookies in the repository. Browser state is kept only in `.local_wechat_profile/`, which is git-ignored.

Run:

```bash
python tools/wechat_browser_importer/importer.py --article-root output/articles
```

Default mode saves a draft. It never mass-sends unless `--publish` is explicitly passed.
