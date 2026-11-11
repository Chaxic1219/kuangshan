# E3 账号企业化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 本机真实登录、首登强制改密、密码≥8 且含字母数字、右上角修改密码。

**Architecture:** `data/users.json` + PBKDF2；会话令牌；`/api/auth/login|refresh|change-password`。前端登录改为调真实 API；`mustChangePassword` 路由闸门。

**Tech Stack:** Python hashlib/secrets、unittest、`frontend_thin`

---

### Task 1: 用户与会话后端
### Task 2: 控制台路由豁免与接线
### Task 3: 前端登录/改密/菜单
### Task 4: 测试构建验收
