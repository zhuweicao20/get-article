# paper_wechat_workflow_reference_push_style_v5

科研论文 PDF → 参考推文风格公众号图文稿。

本版按用户提供的参考推文调整，不再采用上一版“蓝色小标题卡片”的公众号模板，而是模拟参考文章的连续阅读样式：

```text
黑色标题：MOF玻璃，最新Nature Chemistry！
公众号元信息行：高分子科学前沿　时间　地区　听全文
顶部论文/作者/DOI信息
加粗导语
连续正文叙事
大图
图注
图后解读段落
结尾总结
```

广告图、推广服务、留言区等模块默认不生成。

## 视觉排版特征

- 页面最大宽度约 720px；
- 标题为黑色粗体，字号约 22px；
- 公众号来源/时间为灰色小字；
- 正文 16px，行距约 1.8；
- 重点句使用加粗，重要结论倾向橙色，关键词/论文信息倾向蓝色；
- 图片直接接近正文全宽，不加卡片阴影；
- 图注左对齐/两端对齐，字号约 14px；
- 不自动插入广告位。

## 使用

```bash
pip install -r requirements.txt
python src/main.py --pdf your_paper.pdf --out outputs --word-count 1800 --max-figures 8
```

输出：

```text
outputs/
├── article.md
├── article.html
├── article_rich.md
├── article_rich.html
├── article.json
├── selected_charts.json
├── review_report.json
├── outputs.zip
└── images/
```

说明：工作流负责基础解析、图片裁剪、HTML/Markdown 导出；公开发布前仍建议人工检查图表版权、图像裁剪质量和中文表述准确性。
