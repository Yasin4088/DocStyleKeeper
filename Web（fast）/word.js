// 页面加载完成后执行
window.addEventListener('DOMContentLoaded', function() {
    const API_BASE_URL = window.location.protocol === 'file:'
        ? 'http://127.0.0.1:5001'
        : window.location.origin;
    const backendStatusEl = document.getElementById('backendStatus');
    const startProcessBtn = document.getElementById('startProcess');
    const progressPanel = document.getElementById('progressPanel');
    const progressTitle = document.getElementById('progressTitle');
    const progressPercent = document.getElementById('progressPercent');
    const progressBar = document.getElementById('progressBar');
    const progressMessage = document.getElementById('progressMessage');
    const progressLog = document.getElementById('progressLog');
    let backendReady = false;
    let deepseekReady = false;

    function setBackendStatus(state, text) {
        backendStatusEl.className = `backend-status ${state}`;
        backendStatusEl.textContent = text;
    }

    async function checkBackendStatus() {
        setBackendStatus('checking', '正在检查本地后端连接...');
        try {
            const res = await fetch(`${API_BASE_URL}/health`, {
                method: 'GET',
                cache: 'no-store'
            });
            const data = await res.json();
            backendReady = res.ok && data.code === 200;
            deepseekReady = Boolean(data.deepseek && data.deepseek.ok);
            if (backendReady && !deepseekReady) {
                setBackendStatus('offline', data.deepseek?.msg || '本地后端已启动，但 DeepSeek API 不可用');
                return false;
            }
        } catch (e) {
            backendReady = false;
            deepseekReady = false;
        }

        if (backendReady && deepseekReady) {
            setBackendStatus('online', '本地后端与 DeepSeek API 已连接，可以上传并处理 Word 文件');
        } else {
            setBackendStatus('offline', '本地后端未启动：请先运行“启动网页.bat”');
        }
        return backendReady && deepseekReady;
    }

    function resetProgress() {
        progressPanel.style.display = 'none';
        progressTitle.textContent = '等待处理';
        progressPercent.textContent = '0%';
        progressBar.style.width = '0%';
        progressMessage.textContent = '提交任务后会显示 DeepSeek 处理进度。';
        progressLog.innerHTML = '';
    }

    function updateProgress(job) {
        const percent = Math.max(0, Math.min(Number(job.progress || 0), 100));
        progressPanel.style.display = 'block';
        progressTitle.textContent = job.status === 'done'
            ? '处理完成'
            : job.status === 'failed'
                ? '处理失败'
                : 'DeepSeek 正在处理';
        progressPercent.textContent = `${percent}%`;
        progressBar.style.width = `${percent}%`;
        progressMessage.textContent = job.message || '正在处理...';
        progressLog.innerHTML = (job.logs || [])
            .map(item => `<div>${escapeHtml(item)}</div>`)
            .join('');
        progressLog.scrollTop = progressLog.scrollHeight;
    }

    function wait(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    async function pollJob(jobId) {
        while (true) {
            const res = await fetch(`${API_BASE_URL}/jobs/${jobId}`, {
                method: 'GET',
                cache: 'no-store'
            });
            const job = await res.json();
            updateProgress(job);

            if (job.status === 'done') {
                return job;
            }
            if (job.status === 'failed') {
                throw new Error(job.msg || job.message || '处理失败');
            }
            await wait(1000);
        }
    }

    // 页面加载时检查用户状态
    function checkUserStatus() {
        const user = localStorage.getItem('currentUser');
        if (user) {
            // 显示用户头像和退出按钮
            document.querySelector('.user-info').innerHTML = `
                <div class="user-avatar">${user[0]}</div>
                <button class="logout-btn" id="logoutBtn">退出</button>
            `;
            // 添加退出按钮点击事件
            document.getElementById('logoutBtn').addEventListener('click', logout);
            // 启用功能区
            enableFunctionality();
        } else {
            // 显示登录按钮
            document.querySelector('.user-info').innerHTML = `
                <div class="user-avatar" id="loginBtn">登录</div>
            `;
            // 添加登录按钮点击事件
            document.getElementById('loginBtn').addEventListener('click', () => {
                document.getElementById('loginModal').style.display = 'flex';
            });
            // 禁用功能区
            disableFunctionality();
            // 显示登录弹窗
            document.getElementById('loginModal').style.display = 'flex';
        }
    }

    // 启用功能区
    function enableFunctionality() {
        // 移除登录提示事件
        removeLoginPromptEvents();
        
        // 启用导航栏
        document.querySelectorAll('.nav-item').forEach(item => {
            item.style.pointerEvents = 'auto';
            item.style.opacity = '1';
        });
        // 启用功能按钮
        document.getElementById('selectFile').style.pointerEvents = 'auto';
        document.getElementById('selectFile').style.opacity = '1';
        document.getElementById('startProcess').style.pointerEvents = 'auto';
        document.getElementById('startProcess').style.opacity = '1';
        // 启用表单元素（排除登录弹窗中的元素）
        document.querySelectorAll('select, input').forEach(element => {
            // 检查元素是否在登录弹窗中
            if (!element.closest('.login-modal')) {
                element.disabled = false;
            }
        });
    }

    // 禁用功能区
    function disableFunctionality() {
        // 禁用导航栏
        document.querySelectorAll('.nav-item').forEach(item => {
            item.style.pointerEvents = 'auto';
            item.style.opacity = '0.5';
            // 添加点击事件，点击时显示登录弹窗
            item.addEventListener('click', showLoginPrompt);
        });
        
        // 禁用功能按钮
        const buttons = [document.getElementById('selectFile'), document.getElementById('startProcess')];
        buttons.forEach(button => {
            if (button) {
                button.style.pointerEvents = 'auto';
                button.style.opacity = '0.5';
                // 添加点击事件，点击时显示登录弹窗
                button.addEventListener('click', showLoginPrompt);
            }
        });
        
        // 禁用表单元素（排除登录弹窗中的元素）
        document.querySelectorAll('select, input').forEach(element => {
            // 检查元素是否在登录弹窗中
            if (!element.closest('.login-modal')) {
                element.disabled = true;
                // 添加点击事件，点击时显示登录弹窗
                element.addEventListener('click', showLoginPrompt);
            }
        });
        
        // 为工作区的其他部分添加点击事件监听器
        const workspaceElements = document.querySelectorAll('.style-container, .section, .row, .tips');
        workspaceElements.forEach(element => {
            element.style.pointerEvents = 'auto';
            // 添加点击事件，点击时显示登录弹窗
            element.addEventListener('click', showLoginPrompt);
        });
    }
    
    // 显示登录提示
    function showLoginPrompt(e) {
        e.preventDefault();
        e.stopPropagation();
        if (e.stopImmediatePropagation) {
            e.stopImmediatePropagation();
        }
        document.getElementById('loginModal').style.display = 'flex';
    }
    
    // 移除功能区的登录提示事件
    function removeLoginPromptEvents() {
        // 移除导航栏的点击事件
        document.querySelectorAll('.nav-item').forEach(item => {
            item.removeEventListener('click', showLoginPrompt);
        });
        
        // 移除功能按钮的点击事件
        const buttons = [document.getElementById('selectFile'), document.getElementById('startProcess')];
        buttons.forEach(button => {
            if (button) {
                button.removeEventListener('click', showLoginPrompt);
            }
        });
        
        // 移除表单元素的点击事件
        document.querySelectorAll('select, input').forEach(element => {
            // 检查元素是否在登录弹窗中
            if (!element.closest('.login-modal')) {
                element.removeEventListener('click', showLoginPrompt);
            }
        });
        
        // 移除工作区其他部分的点击事件
        const workspaceElements = document.querySelectorAll('.style-container, .section, .row, .tips');
        workspaceElements.forEach(element => {
            element.removeEventListener('click', showLoginPrompt);
        });
    }

    // 导航栏切换逻辑
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            // 切换导航栏激活状态
            document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            
            // 切换右侧面板显示
            const target = item.dataset.target;
            document.querySelectorAll('.panel:not(#style-chart)').forEach(p => p.style.display = 'none');
            if (target) {
                document.getElementById(target).style.display = 'block';
                
                // 如果切换到样式设置面板，则显示字号对照表；否则隐藏字号对照表
                if (target === 'style-panel') {
                    document.getElementById('style-chart').style.display = 'block';
                } else {
                    document.getElementById('style-chart').style.display = 'none';
                }
            }
        });
    });

    // 登录弹窗
    // 登录按钮点击事件会在checkUserStatus函数中动态添加

    // 关闭登录弹窗
    document.getElementById('closeLogin').addEventListener('click', () => {
        document.getElementById('loginModal').style.display = 'none';
    });

    // 登录功能
    function login() {
        console.log('登录功能被调用');
        const user = document.getElementById('username').value;
        console.log('用户名：', user);
        if (user) {
            // 存储用户信息到localStorage
            localStorage.setItem('currentUser', user);
            console.log('用户信息已存储到localStorage');
            // 更新用户信息显示
            document.querySelector('.user-info').innerHTML = `
                <div class="user-avatar">${user[0]}</div>
                <button class="logout-btn" id="logoutBtn">退出</button>
            `;
            // 添加退出按钮点击事件
            document.getElementById('logoutBtn').addEventListener('click', logout);
            // 启用功能区
            enableFunctionality();
            console.log('功能区已启用');
            // 关闭登录弹窗
            document.getElementById('loginModal').style.display = 'none';
            console.log('登录弹窗已关闭');
        } else {
            console.log('用户名不能为空');
            alert('请输入用户名');
        }
    }

    // 登录按钮点击事件
    document.getElementById('submitLogin').addEventListener('click', login);

    // 为用户名和密码输入框添加键盘事件监听器，按下Enter键时登录
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    const togglePassword = document.getElementById('togglePassword');

    usernameInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            login();
        }
    });

    passwordInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            login();
        }
    });

    // 为密码切换按钮添加点击事件，实现密码的显示和隐藏
    togglePassword.addEventListener('click', function() {
        // 切换密码输入框的类型
        const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
        passwordInput.setAttribute('type', type);
        // 切换按钮的图标
        togglePassword.textContent = type === 'password' ? '显示' : '隐藏';
    });

    // 退出功能
    function logout() {
        // 清除localStorage中的用户信息
        localStorage.removeItem('currentUser');
        // 恢复登录按钮
        document.querySelector('.user-info').innerHTML = `
            <div class="user-avatar" id="loginBtn">登录</div>
        `;
        // 添加登录按钮点击事件
        document.getElementById('loginBtn').addEventListener('click', () => {
            document.getElementById('loginModal').style.display = 'flex';
        });
        // 禁用功能区
        disableFunctionality();
        // 清空登录表单内容
        document.getElementById('username').value = '';
        document.getElementById('password').value = '';
        // 显示登录弹窗
        document.getElementById('loginModal').style.display = 'flex';
        // 确保用户界面正确显示
        console.log('用户已退出，跳转到登录界面');
    }

    // 页面加载时检查用户状态
    checkUserStatus();
    checkBackendStatus();
    
    // 业务逻辑（和之前一致）
    let selectedFile = null;
    let historyList = JSON.parse(localStorage.getItem('wordHistory') || '[]');
    
    document.getElementById('selectFile').onclick = () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.docx';
        input.onchange = e => {
            selectedFile = e.target.files[0];
            if (selectedFile) {
                document.getElementById('selectFile').textContent = `已选择：${selectedFile.name}`;
            }
        };
        input.click();
    };

    function fieldValue(selector, fallback = '') {
        const el = document.querySelector(selector);
        return el ? el.value : fallback;
    }
    
    function getStyleConfig() {
        return {
            content: {
                font_cn: fieldValue('.font_cn_content'),
                font_en: fieldValue('.font_en_content'),
                size: fieldValue('.size_content'),
                line: fieldValue('.line_content'),
                first_indent: fieldValue('.first_indent_content', '2'),
                space_before: fieldValue('.space_before_content', '0'),
                space_after: fieldValue('.space_after_content', '0'),
                alignment: fieldValue('.alignment_content', 'justify')
            },
            h1: {
                font_cn: fieldValue('.font_cn_h1'),
                font_en: fieldValue('.font_en_h1'),
                size: fieldValue('.size_h1'),
                bold: fieldValue('.bold_h1'),
                line: fieldValue('.line_h1', '1.5'),
                first_indent: fieldValue('.first_indent_h1', '0'),
                space_before: fieldValue('.space_before_h1', '12'),
                space_after: fieldValue('.space_after_h1', '6'),
                alignment: fieldValue('.alignment_h1', 'center')
            },
            h2: {
                font_cn: fieldValue('.font_cn_h2'),
                font_en: fieldValue('.font_en_h2'),
                size: fieldValue('.size_h2'),
                bold: fieldValue('.bold_h2'),
                line: fieldValue('.line_h2', '1.5'),
                first_indent: fieldValue('.first_indent_h2', '0'),
                space_before: fieldValue('.space_before_h2', '6'),
                space_after: fieldValue('.space_after_h2', '3'),
                alignment: fieldValue('.alignment_h2', 'center')
            },
            h3: {
                font_cn: fieldValue('.font_cn_h3'),
                font_en: fieldValue('.font_en_h3'),
                size: fieldValue('.size_h3'),
                bold: fieldValue('.bold_h3'),
                line: fieldValue('.line_h3', '1.5'),
                first_indent: fieldValue('.first_indent_h3', '0'),
                space_before: fieldValue('.space_before_h3', '3'),
                space_after: fieldValue('.space_after_h3', '3'),
                alignment: fieldValue('.alignment_h3', 'center')
            }
        };
    }
    
    function saveHistory(fileName, url) {
        const item = { name: fileName, time: new Date().toLocaleString(), url: url };
        historyList.unshift(item);
        if (historyList.length > 50) historyList = historyList.slice(0, 50);
        localStorage.setItem('wordHistory', JSON.stringify(historyList));
        renderHistory();
    }

    function persistHistory() {
        localStorage.setItem('wordHistory', JSON.stringify(historyList));
    }

    async function deleteRemoteHistoryFile(url) {
        const match = String(url || '').match(/\/download\/([0-9a-f]{32})(?:[/?#]|$)/i);
        if (!match) return;
        await fetch(`${API_BASE_URL}/download/${match[1]}`, {
            method: 'DELETE',
            cache: 'no-store'
        });
    }

    function deleteHistory(index) {
        const item = historyList[index];
        if (!item) return;
        if (!confirm(`确定删除历史文件“${item.name}”吗？`)) return;

        historyList.splice(index, 1);
        persistHistory();
        renderHistory();

        deleteRemoteHistoryFile(item.url).catch(e => {
            console.warn('历史文件临时数据删除失败：', e);
        });
    }
    
    function renderHistory() {
        const list = document.getElementById('historyList');
        if (historyList.length === 0) {
            list.innerHTML = '<div style="color:#888;text-align:center;padding:20px;">暂无历史记录</div>';
            return;
        }
        list.innerHTML = historyList.map((h, index) => `
            <div class="history-item">
                <div class="history-name">${escapeHtml(h.name)}</div>
                <div class="history-time">${escapeHtml(h.time)}</div>
                <div class="history-actions">
                    <a href="${escapeHtml(h.url)}" download>下载文件</a>
                    <button type="button" class="history-delete" data-index="${index}">删除</button>
                </div>
            </div>
        `).join('');
    }

    document.getElementById('historyList').addEventListener('click', e => {
        const btn = e.target.closest('.history-delete');
        if (!btn) return;
        deleteHistory(Number(btn.dataset.index));
    });
    
    renderHistory();
    
    startProcessBtn.onclick = async () => {
        if (!selectedFile) { alert('请先选择 Word 文件'); return; }

        const online = (backendReady && deepseekReady) || await checkBackendStatus();
        if (!online) {
            alert('本地后端或 DeepSeek API 不可用，请确认服务已启动且 API Key 已填写');
            return;
        }

        const fd = new FormData();
        fd.append('file', selectedFile);
        fd.append('styleConfig', JSON.stringify(getStyleConfig()));

        const oldText = startProcessBtn.textContent;
        startProcessBtn.disabled = true;
        startProcessBtn.textContent = '任务处理中...';
        document.getElementById('resultBox').style.display = 'none';
        resetProgress();
        updateProgress({
            status: 'queued',
            progress: 5,
            message: '正在上传文件并提交 DeepSeek 处理任务...',
            logs: ['正在上传文件并提交 DeepSeek 处理任务...']
        });

        try {
            const res = await fetch(`${API_BASE_URL}/local-process-word`, { method: 'POST', body: fd });
            const data = await res.json();
            if (data.code === 202 && data.jobId) {
                updateProgress({
                    status: 'queued',
                    progress: 5,
                    message: '任务已提交，等待 DeepSeek 处理...',
                    logs: ['任务已提交，等待 DeepSeek 处理...']
                });
                const job = await pollJob(data.jobId);
                document.getElementById('resultBox').style.display = 'block';
                const link = document.getElementById('downloadLink');
                link.href = job.downloadUrl;
                link.download = job.fileName || ('已排版_' + selectedFile.name);
                saveHistory(job.fileName || ('已排版_' + selectedFile.name), job.downloadUrl);
            } else {
                alert('处理失败：' + data.msg);
            }
        } catch (e) {
            updateProgress({
                status: 'failed',
                progress: 100,
                message: e.message || '处理失败',
                logs: [e.message || '处理失败']
            });
            alert(e.message || '处理失败');
        } finally {
            startProcessBtn.disabled = false;
            startProcessBtn.textContent = oldText;
        }
    };
});
