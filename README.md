# Chem Paper → WeChat Article Auto Pipeline

这个仓库用于 GitHub Actions 自动完成：

```text
开放获取化学大类期刊新论文
        ↓
自动筛选关键词
        ↓
自动下载开放 PDF
        ↓
调用已有公众号工作流和 skill
        ↓
生成 article.md / article.html / images / outputs.zip
        ↓
上传 GitHub Actions artifact
```

本包已经把你当前的期刊监控脚本升级为“自动下载 PDF + 自动处理工作流”的版本，并保留了原脚本：

```text
scripts/legacy_journal_monitor.py
```

## 1. 上传到 GitHub

本地解压后进入目录：

```bash
git init
git add .
git commit -m "init auto chem wechat pipeline"
git branch -M main
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

建议仓库先设成 Private。

## 2. 设置 Unpaywall 邮箱 Secret

GitHub 仓库页面：

```text
Settings
→ Secrets and variables
→ Actions
→ New repository secret
```

添加：

```text
Name: UNPAYWALL_EMAIL
Value: 你的邮箱
```

这个邮箱只用于 Unpaywall API 查询开放 PDF。

## 3. 手动测试一次

进入 GitHub 仓库：

```text
Actions
→ Auto Chem Paper to WeChat Article
→ Run workflow
```

第一次默认只建立 DOI 去重库，防止一下子处理历史文章。如果你要强制测试，点 Run workflow 时把：

```text
force = true
```

然后运行。

## 4. 查看结果

运行结束后，在 Actions 详情页下载 artifact：

```text
chem-wechat-article-packages
```

里面会有：

```text
output/articles/001_xxx/
├─ article.md
├─ article_rich.md
├─ article.html
├─ article_rich.html
├─ article.pdf               # 若 WeasyPrint 成功
├─ images/
├─ paper.json
├─ title.txt
├─ source.txt
├─ outputs.zip
└─ workflow_run.log

output/report.csv
output/candidates.json
```

## 5. 修改监控期刊

期刊列表在：

```text
config/journals.yml
```

关键词在：

```text
config/keywords.yml
```

默认偏化学大类：催化、光电催化、降解、水处理、MOF/COF/MXene、能源材料、DFT、机器学习等。

## 6. 修改每天生成数量

在：

```text
config/pipeline.yml
```

可改：

```yaml
max_downloads: 5
max_articles: 2
word_count: 1800
max_figures: 8
```

## 7. 注意

- 这个流程只下载开放获取 PDF，不绕过学校订阅权限。
- Nature Communications / Communications Chemistry / ACS Central Science / Science Advances 等开放文章更容易成功。
- JACS 普通订阅文章不建议放自动下载；JACS Au 和 ACS Central Science 更适合。
- 公众号“保存草稿”需要服务器浏览器自动化脚本另接，本仓库当前先负责“找论文、下载、生成文章包”。
