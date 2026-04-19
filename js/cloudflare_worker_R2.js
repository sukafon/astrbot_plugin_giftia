export default {
    async fetch(request, env) {
        // 鉴权，请在Worker的环境变量中配置 X-Auth-Token，把设置的值填入插件配置
        if (request.headers.get("X-Auth-Token") !== env.X_AUTH_TOKEN) {
            return new Response("Unauthorized", { status: 401 });
        }

        // 提取路径中的文件名
        const url = new URL(request.url);
        const key = url.pathname.slice(1);

        switch (request.method) {
            case "PUT": {
                // 这里的R2是在Worker中绑定存储桶的时候创建的环境变量，不是存储桶名称，可以改成别的
                await env.R2.put(key, request.body, {
                    onlyIf: request.headers,
                    httpMetadata: request.headers,
                });
                return new Response(`Put ${key} successfully!`);
            }
            // 为了保证数据安全，这里移除了GET和DELETE方法
            default:
                return new Response("Method Not Allowed", {
                    status: 405,
                    headers: {
                        Allow: "PUT",
                    },
                });
        }
    }
}