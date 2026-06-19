# AGV-TMS Frontend — Production Dockerfile (Multi-stage Build)
#
# Phase 5: 工程化加固
#   - Stage 1: npm ci + vite build (构建静态文件)
#   - Stage 2: nginx serve (轻量级生产服务器)
#   - Gzip 压缩 + 缓存策略 + 安全头

# ==================== Stage 1: Build ====================
FROM node:22-alpine AS builder

WORKDIR /build

# 复制依赖文件
COPY package.json package-lock.json* ./

# 安装依赖
RUN npm ci --legacy-peer-deps

# 复制源码并构建
COPY . .
RUN npm run build

# ==================== Stage 2: Serve with Nginx ====================
FROM nginx:1.25-alpine AS runtime

# 元数据
LABEL maintainer="AGV-TMS Team"
LABEL description="AGV-TMS Frontend - Static Files"
LABEL version="1.8.0"

# 自定义 nginx 配置 (生产优化)
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 3000;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types
        text/plain
        text/css
        text/xml
        text/javascript
        application/javascript
        application/json
        application/xml+rss
        image/svg+xml;

    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # SPA 路由支持 (React Router History mode)
    location / {
        try_files $uri $uri/ /index.html;

        # 静态资源缓存策略
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }

        # API 反向代理到后端
        location /api/ {
            proxy_pass http://backend:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            # WebSocket 支持
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            
            # 超时设置
            proxy_connect_timeout 10s;
            proxy_send_timeout 30s;
            proxy_read_timeout 60s;
        }

        # 健康检查端点
        location = /health {
            access_log off;
            return 200 'OK';
            add_header Content-Type text/plain;
        }
    }

    # 禁止访问隐藏文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
EOF

# 从构建阶段复制静态文件
COPY --from=builder /build/dist /usr/share/nginx/html

# 暴露端口
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s \
    CMD wget -qO- http://localhost:3000/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
