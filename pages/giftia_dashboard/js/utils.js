// Giftia Dashboard Utilities

window.formatDate = function(isoString) {
    if (!isoString) return "-";
    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) {
            return isoString;
        }
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, "0");
        const d = String(date.getDate()).padStart(2, "0");
        const h = String(date.getHours()).padStart(2, "0");
        const min = String(date.getMinutes()).padStart(2, "0");
        const s = String(date.getSeconds()).padStart(2, "0");
        return `${y}-${m}-${d} ${h}:${min}:${s}`;
    } catch {
        return isoString;
    }
};

window.escapeHtml = function(text) {
    if (!text) return "";
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
};

window.showToast = function(message) {
    const toast = document.getElementById("toast-message");
    if (toast) {
        toast.textContent = message;
        toast.classList.add("show");
        setTimeout(() => {
            toast.classList.remove("show");
        }, 3000);
    }
};

window.copyToClipboard = function(text, label) {
    if (!text) return;
    
    const successMessage = `${label || "内容"}已复制到剪贴板`;
    
    const fallbackCopy = (val) => {
        try {
            const textArea = document.createElement("textarea");
            textArea.value = val;
            textArea.style.top = "0";
            textArea.style.left = "0";
            textArea.style.position = "fixed";
            textArea.style.opacity = "0";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            const successful = document.execCommand("copy");
            document.body.removeChild(textArea);
            if (successful) {
                window.showToast(successMessage);
            } else {
                window.showToast("复制失败，请手动复制");
            }
        } catch (err) {
            console.error("fallbackCopy 复制失败:", err);
            window.showToast("复制失败，请手动复制");
        }
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            window.showToast(successMessage);
        }).catch(err => {
            console.error("navigator.clipboard 复制失败:", err);
            fallbackCopy(text);
        });
    } else {
        fallbackCopy(text);
    }
};

window.renderPagination = function(containerId, state, onPageChange) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const totalPages = Math.ceil(state.total / state.limit);
    if (totalPages <= 1) {
        container.innerHTML = `<span>共 ${state.total} 条记录</span>`;
        return;
    }

    const startItem = (state.page - 1) * state.limit + 1;
    const endItem = Math.min(state.page * state.limit, state.total);

    let buttons = "";
    buttons += `<button class="pagination-btn" ${state.page === 1 ? "disabled" : ""} data-page="${state.page - 1}">上一页</button>`;
    
    const startPage = Math.max(1, state.page - 2);
    const endPage = Math.min(totalPages, state.page + 2);
    
    for (let i = startPage; i <= endPage; i++) {
        buttons += `<button class="pagination-btn ${state.page === i ? "active" : ""}" data-page="${i}">${i}</button>`;
    }
    
    buttons += `<button class="pagination-btn" ${state.page === totalPages ? "disabled" : ""} data-page="${state.page + 1}">下一页</button>`;

    container.innerHTML = `
        <span>显示 ${startItem}-${endItem} 条，共 ${state.total} 条</span>
        <div class="pagination-buttons">
            ${buttons}
        </div>
    `;

    const btns = container.querySelectorAll(".pagination-btn");
    btns.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetPage = parseInt(btn.getAttribute("data-page"));
            if (targetPage && targetPage !== state.page) {
                onPageChange(targetPage);
            }
        });
    });
};

window.formatBytes = function(bytes) {
    if (bytes === 0) return "0 字节";
    const k = 1024;
    const sizes = ["字节", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
};

// Confirm Modal logic
let confirmCallback = null;

window.showConfirm = function(title, message, callback) {
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-message").textContent = message;
    confirmCallback = callback;
    window.openModal("confirm-modal");
};

window.closeConfirmModal = function() {
    window.closeModal("confirm-modal");
    confirmCallback = null;
};

document.addEventListener("DOMContentLoaded", () => {
    const confirmBtnOk = document.getElementById("confirm-btn-ok");
    if (confirmBtnOk) {
        confirmBtnOk.addEventListener("click", () => {
            if (confirmCallback) {
                confirmCallback();
            }
            window.closeConfirmModal();
        });
    }
});

window.downloadMedia = async function(hash, mimeType) {
    try {
        window.showToast("正在准备下载...");
        const res = await window.apiGet(`/media/file/b64/${hash}`);
        if (res && res.status === "success" && res.base64) {
            const actualMime = res.content_type || mimeType || "audio/mpeg";
            const byteCharacters = atob(res.base64);
            const byteNumbers = new Array(byteCharacters.length);
            for (let i = 0; i < byteCharacters.length; i++) {
                byteNumbers[i] = byteCharacters.charCodeAt(i);
            }
            const byteArray = new Uint8Array(byteNumbers);
            const blob = new Blob([byteArray], { type: actualMime });
            const url = URL.createObjectURL(blob);
            
            const a = document.createElement("a");
            a.href = url;
            const ext = actualMime.replace("audio/", "").replace("voice/", "") || "amr";
            a.download = `${hash}.${ext.toLowerCase()}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } else {
            window.showToast("获取音频文件失败");
        }
    } catch (e) {
        console.error("Failed to download media:", e);
        window.showToast("下载失败: " + e.message);
    }
};
