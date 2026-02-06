FROM python:3.12-slim

WORKDIR /app

# 先做系统依赖安装（和代码无关，便于缓存）
# 使用国内 apt 源 + 安装 Node.js
# 注意：python:3.12-slim 默认可能没有 /etc/apt/sources.list，这里直接覆盖为阿里源
RUN set -eux; \
    . /etc/os-release; \
    codename="${VERSION_CODENAME:-stable}"; \
    echo "deb http://mirrors.aliyun.com/debian ${codename} main contrib non-free non-free-firmware" > /etc/apt/sources.list; \
    echo "deb http://mirrors.aliyun.com/debian ${codename}-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    echo "deb http://mirrors.aliyun.com/debian-security ${codename}-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends nodejs; \
    rm -rf /var/lib/apt/lists/*

# 只拷贝依赖文件，单独一层，便于 pip 缓存
COPY requirements.txt ./

# 安装 Python 依赖：指定阿里云pip国内镜像，--no-cache-dir减少镜像体积
RUN pip install --no-cache-dir \
    --index-url https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

# 安装 Playwright 浏览器（只装 Chromium，带系统依赖）
RUN python -m playwright install --with-deps chromium

# 再拷贝项目代码（改代码只会影响这一层和之后）
COPY . /app

# 设置启动命令
CMD ["python", "main.py"]