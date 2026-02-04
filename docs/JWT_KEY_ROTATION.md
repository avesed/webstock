# JWT 密钥轮换指南

## 概述

本项目支持 **平滑密钥轮换**，可以在不强制所有用户重新登录的情况下更换 JWT 签名密钥。

## 工作原理

1. **主密钥 (JWT_SECRET_KEY)**: 用于签发新的 token
2. **历史密钥 (JWT_SECRET_KEY_PREVIOUS)**: 用于验证旧 token
3. **平滑过渡**: 轮换后，旧 token 仍然有效直到过期，新 token 使用新密钥

## 快速使用

### 1. 查看当前密钥状态

```bash
cd backend
python scripts/rotate_keys.py --status
```

### 2. 执行密钥轮换

```bash
cd backend
python scripts/rotate_keys.py --rotate
```

输出示例：
```
============================================================
JWT Key Rotation
============================================================
Timestamp: 2026-02-02T10:30:00

Current Configuration:
  Primary: 8b591ee7...dc36316c
  Previous keys: 0

New Configuration:
  Primary: a1b2c3d4...e5f67890
  Previous keys: 1
    [1] 8b591ee7...dc36316c

Environment Variables to Update:
------------------------------------------------------------
JWT_SECRET_KEY=a1b2c3d4e5f67890... (新密钥)
JWT_SECRET_KEY_PREVIOUS=8b591ee7736fe21d7f57bc1cbc7025d213f38b74ea67dd33daebd5b0dc36316c
------------------------------------------------------------
```

### 3. 更新环境变量

复制输出的新配置到你的 `.env` 文件：

```bash
# .env
JWT_SECRET_KEY=a1b2c3d4... (新密钥)
JWT_SECRET_KEY_PREVIOUS=8b591ee7... (旧密钥)
```

### 4. 重启服务

```bash
# Docker
docker-compose restart backend

# 或者直接重启
python -m app.main
```

### 5. 清理旧密钥（可选）

等待 24-48 小时（超过 token 最大有效期），确认所有旧 token 都已过期后，可以从 `.env` 中移除旧密钥：

```bash
# .env
JWT_SECRET_KEY=a1b2c3d4... (当前密钥)
# 删除或注释掉 JWT_SECRET_KEY_PREVIOUS
```

## 其他命令

### 验证密钥配置

```bash
python scripts/rotate_keys.py --validate
```

### 紧急替换所有密钥（强制所有用户重新登录）

⚠️ **警告**: 这会使所有现有 token 失效，所有用户需要重新登录！

```bash
python scripts/rotate_keys.py --emergency
```

## 生产环境建议

### 1. 使用密钥管理系统（KMS）

生产环境建议使用专业的密钥管理系统：

- **HashiCorp Vault**
- **AWS Secrets Manager**
- **Azure Key Vault**
- **Google Secret Manager**

### 2. 定期轮换计划

建议的轮换频率：
- **高安全要求**: 每 30 天
- **一般要求**: 每 90 天
- **最低要求**: 每年或怀疑泄露时

### 3. 监控密钥使用情况

```python
# 在应用中监控
from app.core.security import get_key_rotation_info

info = get_key_rotation_info()
print(f"Previous keys: {info['previous_keys_count']}")
```

### 4. 自动化轮换流程

可以设置 CI/CD 自动轮换：

```yaml
# .github/workflows/rotate-keys.yml
name: Rotate JWT Keys
on:
  schedule:
    - cron: '0 2 1 * *'  # 每月 1 日凌晨 2 点

jobs:
  rotate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Rotate keys
        run: |
          python scripts/rotate_keys.py --rotate
          # 自动更新 secrets（需要配合 KMS API）
```

## 故障排除

### Token 验证失败

检查日志中的 `[Auth] Refresh failed`，如果显示 `Signature verification failed`，说明：

1. 密钥配置不正确
2. token 使用了已被移除的旧密钥签名

**解决方法**:
- 用户需要重新登录获取新 token
- 或者在 `.env` 中添加对应的历史密钥

### 密钥泄露应急响应

如果怀疑密钥泄露：

1. **立即执行紧急替换**:
   ```bash
   python scripts/rotate_keys.py --emergency
   ```

2. **更新环境变量并重启**

3. **通知所有用户重新登录**

4. **审计日志**: 检查是否有异常登录行为

## 安全最佳实践

1. ✅ **密钥长度**: 至少 32 字节（64 字符十六进制）
2. ✅ **随机生成**: 使用 `secrets.token_hex(32)` 或 `openssl rand -hex 32`
3. ✅ **安全存储**: 不要提交到 git，使用环境变量或 KMS
4. ✅ **定期轮换**: 建立定期轮换计划
5. ✅ **监控审计**: 记录密钥使用情况和异常行为
6. ❌ **不要**: 使用简单密码或示例密钥
7. ❌ **不要**: 在前端暴露密钥
8. ❌ **不要**: 在日志中打印完整密钥

## 联系支持

如有问题，请检查：
1. 环境变量是否正确加载
2. 密钥格式是否正确（64 字符十六进制）
3. 服务是否正确重启
4. 查看应用日志中的详细错误信息
