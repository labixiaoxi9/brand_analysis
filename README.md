# FastAPI + Coze 智能体对接（可部署 Vercel）

这个项目实现了：

- 一个 FastAPI 后端接口：`POST /api/chat`
- 后端调用 Coze 智能体 `stream_run` 接口
- 解析 SSE 返回并提取 `answer` 内容
- 一个简单的 HTML 页面用于输入问题并显示回答

## 目录结构

- `api/main.py`：FastAPI 应用与 Coze 调用逻辑
- `templates/index.html`：前端页面
- `requirements.txt`：Python 依赖
- `vercel.json`：Vercel 部署配置

## 1) 本地运行

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置 `.env`

在项目根目录新建 `.env`：

```env
COZE_API_TOKEN=你的 Coze Token
COZE_SESSION_ID=gMn8iGKLJRRUcirl2Ymgv
COZE_PROJECT_ID=7621065736169242675
```

### 启动服务

```bash
uvicorn api.main:app --reload --port 8000
```

打开浏览器访问：

- `http://127.0.0.1:8000/`

## 2) 接口说明

### `POST /api/chat`

表单参数（`multipart/form-data`）：

- `text`：必填，用户输入文本
- `api_token`：可选，若不传则使用环境变量 `COZE_API_TOKEN`

返回示例：

```json
{
  "ok": true,
  "answer": "智能体回复内容",
  "events": [
    {"event": "message", "data": {"type": "answer", "content": "..."}}
  ]
}
```

## 3) 部署到 Vercel

### 新建项目并导入仓库/目录

确保 Vercel 使用项目根目录作为 Root。

### 配置环境变量

在 Vercel 项目设置中添加：

- `COZE_API_TOKEN`
- `COZE_SESSION_ID`
- `COZE_PROJECT_ID`

### 部署

Vercel 会根据 `vercel.json` 使用 `@vercel/python` 启动 `api/main.py`。

部署后访问域名根路径即可打开页面。

## 4) 安全建议

- 不要把真实 Token 硬编码到代码里。
- 推荐只通过环境变量设置 `COZE_API_TOKEN`。
- 前端输入的 token 仅用于调试，生产环境建议移除该输入框。
