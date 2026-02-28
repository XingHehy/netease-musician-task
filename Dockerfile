# 多阶段构建：浏览器下载放在独立阶段，只有 requirements 或基础镜像变更时才重下；改代码只影响最后一层

# ---------- 阶段 1：仅下载 Chromium，便于 Docker 层缓存 ----------
# 只有 requirements.txt / 基础镜像变化时此阶段才重建，改业务代码不会触发重下浏览器
FROM python:3.12-slim AS playwright-browser
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir \
    --index-url https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
RUN python -m playwright install chromium

# ---------- 阶段 2：最终运行镜像 ----------
FROM python:3.12-slim

WORKDIR /app

# 系统依赖（apt 源 + Node.js，Playwright 需要）
RUN set -eux; \
    . /etc/os-release; \
    codename="${VERSION_CODENAME:-stable}"; \
    echo "deb http://mirrors.aliyun.com/debian ${codename} main contrib non-free non-free-firmware" > /etc/apt/sources.list; \
    echo "deb http://mirrors.aliyun.com/debian ${codename}-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    echo "deb http://mirrors.aliyun.com/debian-security ${codename}-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends nodejs; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir \
    --index-url https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

# 只装 Chromium 的系统依赖；浏览器二进制从上一阶段拷贝，不重复下载
RUN python -m playwright install-deps chromium
COPY --from=playwright-browser /root/.cache/ms-playwright /root/.cache/ms-playwright

# 项目代码（改代码只影响本层及之后，前面的浏览器层继续用缓存）
COPY . /app

CMD ["python", "main.py"]
