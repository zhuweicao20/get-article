# WeChat Publishing

This repo supports two publishing paths. Both default to draft mode.

## A. Official WeChat API

Use this when the Official Account has API permissions.

Required GitHub Actions secrets:

- `UNPAYWALL_EMAIL`
- `WECHAT_APPID`
- `WECHAT_APPSECRET`

Optional secrets:

- `WECHAT_AUTHOR`
- `WECHAT_SOURCE_URL`
- `WECHAT_NEED_OPEN_COMMENT`
- `WECHAT_ONLY_FANS_CAN_COMMENT`

Workflows:

- `Auto Chem Paper to WeChat Article`: runs every 2 hours or manually. It reads `config/journals.yml`, `config/keywords.yml`, `config/pipeline.yml`, downloads open-access PDFs only, generates `output/articles/...`, and uploads artifact `chem-wechat-article-packages`.
- `Publish Generated Article to WeChat`: manually downloads the latest article artifact and creates a WeChat draft. Use `mode=draft` first.
- `Auto Chem Paper to WeChat Publish`: manually generates one article and sends it to WeChat. Default inputs are `force=false`, `days=3`, `max_articles=1`, `mode=draft`.

The publisher reads the newest `output/articles/...` folder, prefers `article_rich.html`, falls back to `article.html`, uploads local content images through `media/uploadimg`, uploads the cover through `material/add_material`, creates a draft through `draft/add`, and only calls `freepublish/submit` when `mode=publish`.

If `WECHAT_APPID` or `WECHAT_APPSECRET` is missing, the workflow stops and asks you to add them in GitHub Secrets. Do not put secrets in code, README, workflows, or logs.

## B. Local Browser Import

Use this when the account cannot use the official API.

Run on your own computer:

```bat
run_wechat_importer.bat
```

The importer opens `https://mp.weixin.qq.com/`, waits for your QR-code login, opens the draft editor, reads the newest local `article.html` or `article_rich.html`, fills the title and body, and leaves the browser open for review.

It does not save passwords. Browser state is stored only in `.local_wechat_profile/`, which is ignored by git. It does not auto mass-send. Publishing requires explicit manual review, and the script still does not implement automatic group sending.

## Artifacts

Generated article packages should contain:

- `article.md`
- `article_rich.md`
- `article.html`
- `article_rich.html`
- `article.pdf`
- `images/`
- `paper.json`
- `title.txt`
- `source.txt`
- `outputs.zip`
- `workflow_run.log`

The run also uploads:

- `output/report.csv`
- `output/candidates.json`
