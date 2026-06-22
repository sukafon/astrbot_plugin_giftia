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
