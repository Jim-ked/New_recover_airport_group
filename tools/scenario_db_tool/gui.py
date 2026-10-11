"""Offline desktop entrypoint: python -m tools.scenario_db_tool.gui."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .gui_service import GuiService, GuiServiceError, launch_exit_message, redact
from .models import ScenarioToolError


class ScenarioGui:
    def __init__(self, window: tk.Tk, service: GuiService | None = None):
        self.window = window
        self.service = service or GuiService()
        self.events = queue.Queue()
        self.busy = False
        self.status_pending = False
        self.closed = False
        self.launch_stop_event = None
        self.current_status = {'running': False, 'web_pids': [], 'worker_pids': []}
        self.scenes = {}
        window.title('机场群场景库管理工具')
        window.geometry('900x600')
        window.minsize(820, 560)
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.excel_path = tk.StringVar()
        self.selected_scene = tk.StringVar()
        self.status_text = tk.StringVar(value='正在读取系统状态…')
        self.pid_text = tk.StringVar(value='Web PID：—    Worker PID：—')
        self.database_text = tk.StringVar(value='')
        self._layout()
        self.refresh_scenes()
        self.window.after(100, self._drain)
        self.window.after(200, self._poll_status)

    def _layout(self):
        style = ttk.Style(self.window)
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 17, 'bold'))
        style.configure('Status.TLabel', font=('Microsoft YaHei UI', 12, 'bold'))
        shell = ttk.Frame(self.window, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='机场群场景库管理工具', style='Title.TLabel').pack(anchor='w')
        self.notebook = ttk.Notebook(shell)
        self.notebook.pack(fill='both', expand=True, pady=(12, 10))
        scenes = ttk.Frame(self.notebook, padding=12)
        system = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(scenes, text='场景管理')
        self.notebook.add(system, text='系统启动')
        row = ttk.Frame(scenes)
        row.pack(fill='x')
        ttk.Entry(row, textvariable=self.excel_path, state='readonly').pack(side='left', fill='x', expand=True)
        self.choose_button = ttk.Button(row, text='选择Excel文件', command=self.choose_excel)
        self.choose_button.pack(side='left', padx=(8, 0))
        actions = ttk.Frame(scenes)
        actions.pack(fill='x', pady=8)
        self.check_button = ttk.Button(actions, text='检查Excel', command=self.check_excel)
        self.check_button.pack(side='left')
        self.build_button = ttk.Button(actions, text='生成场景库', command=self.build_scene)
        self.build_button.pack(side='left', padx=8)
        ttk.Label(actions, text='生成结果保存在 runtime/db/scenes').pack(side='left', padx=4)
        self.validation_text = ScrolledText(scenes, height=4, wrap='word', font=('Microsoft YaHei UI', 9))
        self.validation_text.pack(fill='x')
        self._validation('请选择 .xlsx 文件，然后检查或生成场景库。')
        listing = ttk.Frame(scenes)
        listing.pack(fill='both', expand=True, pady=(10, 0))
        heading = ttk.Frame(listing)
        heading.pack(fill='x')
        ttk.Label(heading, text='已有场景库（只读）').pack(side='left')
        self.refresh_button = ttk.Button(heading, text='刷新列表', command=self.refresh_scenes)
        self.refresh_button.pack(side='right')
        table = ttk.Frame(listing)
        table.pack(fill='both', expand=True, pady=(5, 0))
        self.tree = ttk.Treeview(table, columns=('name', 'created', 'size'), show='headings', height=5)
        for key, title, width in [('name', '文件名', 430), ('created', '创建时间', 170), ('size', '大小', 110)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=key == 'name')
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', self.select_scene)
        default = ttk.LabelFrame(system, text='默认系统', padding=10)
        default.pack(fill='x')
        ttk.Label(default, text='使用 runtime/db/airport_group.sqlite3').pack(side='left')
        self.start_button = ttk.Button(default, text='启动系统', command=lambda: self.start(False))
        self.start_button.pack(side='right')
        selected = ttk.LabelFrame(system, text='场景系统', padding=10)
        selected.pack(fill='x', pady=10)
        self.combo = ttk.Combobox(selected, textvariable=self.selected_scene, state='readonly')
        self.combo.pack(side='left', fill='x', expand=True)
        self.scene_start_button = ttk.Button(selected, text='启动场景', command=lambda: self.start(True))
        self.scene_start_button.pack(side='right', padx=(10, 0))
        status = ttk.LabelFrame(system, text='系统状态', padding=10)
        status.pack(fill='x')
        ttk.Label(status, textvariable=self.status_text, style='Status.TLabel').pack(anchor='w')
        ttk.Label(status, textvariable=self.pid_text).pack(anchor='w', pady=5)
        ttk.Label(status, textvariable=self.database_text, wraplength=780).pack(anchor='w')
        controls = ttk.Frame(system)
        controls.pack(fill='x', pady=10)
        self.status_button = ttk.Button(controls, text='刷新状态', command=self.refresh_status)
        self.status_button.pack(side='left')
        self.stop_button = ttk.Button(controls, text='停止系统', command=self.stop)
        self.stop_button.pack(side='left', padx=8)
        ttk.Label(shell, text='操作日志').pack(anchor='w')
        self.log_text = ScrolledText(shell, height=6, state='disabled', wrap='word', font=('Microsoft YaHei UI', 9))
        self.log_text.pack(fill='x', pady=(4, 0))
        self.action_buttons = [self.choose_button, self.check_button, self.build_button,
                               self.start_button, self.scene_start_button, self.stop_button]

    def log(self, message):
        message = redact(str(message))
        self.service.logger.info(message)
        self.log_text.configure(state='normal')
        self.log_text.insert('end', f'[{datetime.now():%H:%M:%S}] {message}\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def _validation(self, message):
        self.validation_text.configure(state='normal')
        self.validation_text.delete('1.0', 'end')
        self.validation_text.insert('end', redact(message))
        self.validation_text.configure(state='disabled')

    def choose_excel(self):
        path = filedialog.askopenfilename(parent=self.window, title='选择场景 Excel', filetypes=[('Excel 工作簿', '*.xlsx')])
        if path:
            self.excel_path.set(path)
            self._validation('已选择文件，请检查Excel。')
            self.log('已选择 Excel 文件')

    def _task(self, action, callback):
        if self.busy:
            return
        self.busy = True
        for button in self.action_buttons:
            button.configure(state='disabled')
        def work():
            try:
                result = action()
                self.events.put(('done', callback, result))
            except Exception as exc:
                self.service.logger.exception('操作失败')
                self.events.put(('error', None, exc))
        threading.Thread(target=work, daemon=True).start()

    def _finish(self):
        self.busy = False
        for button in self.action_buttons:
            button.configure(state='normal')

    def _drain(self):
        if self.closed:
            return
        while not self.events.empty():
            kind, callback, value = self.events.get_nowait()
            if kind in ('done', 'error'):
                self._finish()
            if kind == 'done':
                callback(value)
            elif kind == 'error':
                if isinstance(value, ScenarioToolError):
                    message = '\n'.join(f'{i.sheet} / 第{i.row}行 / {i.field}：{i.message}' for i in value.issues)
                    self._validation(message)
                else:
                    message = str(value) if isinstance(value, GuiServiceError) else '操作失败，请查看 runtime/logs/scenario_gui.log。'
                self.log(message)
                messagebox.showerror('操作失败', redact(message), parent=self.window)
            elif kind == 'status':
                self.status_pending = False
                self._show_status(value)
            elif kind == 'status_error':
                self.status_pending = False
                self.status_text.set('状态读取失败')
                self.log(str(value))
            elif kind == 'log':
                self.log(value)
            elif kind == 'launch_exit':
                code, stop_event = value
                message = launch_exit_message(code, stop_event.is_set())
                if message:
                    self.log(message)
                    messagebox.showerror('启动失败', message, parent=self.window)
        self.window.after(100, self._drain)

    def check_excel(self):
        path = self.excel_path.get()
        self.log('Excel验证开始')
        def show(result):
            if result.ok:
                labels = {'airports': '机场', 'aircraft_types': '机型', 'resources': '资源',
                          'missions': '任务', 'damage_scenarios': '损毁方案'}
                self._validation('验证通过\n' + '    '.join(f'{labels[k]}：{v}' for k, v in result.counts.items()))
                self.log('验证通过')
            else:
                self._validation('验证失败\n' + '\n'.join(f'{i.sheet} / 第{i.row}行 / {i.field}：{i.message}' for i in result.issues))
                self.log(f'验证失败，共 {len(result.issues)} 项问题')
        self._task(lambda: self.service.validate_excel(path), show)

    def build_scene(self):
        path = self.excel_path.get()
        self.log('场景库生成开始')
        def show(result):
            database, scenes = result
            self.refresh_scenes(scenes)
            self.selected_scene.set(database.name)
            self._validation(f'场景库生成成功：\n{database}')
            self.log(f'场景库生成成功：{database.name}')
        self._task(lambda: self.service.build_scene(path), show)

    def refresh_scenes(self, scenes=None):
        try:
            scenes = self.service.list_scenes() if scenes is None else scenes
            self.scenes = {item.path.name: item.path for item in scenes}
            self.tree.delete(*self.tree.get_children())
            for item in scenes:
                self.tree.insert('', 'end', iid=item.path.name,
                                 values=(item.path.name, item.created_at.strftime('%Y-%m-%d %H:%M:%S'), f'{item.size / 1024:,.1f} KB'))
            self.combo.configure(values=list(self.scenes))
            if self.selected_scene.get() not in self.scenes:
                self.selected_scene.set(next(iter(self.scenes), ''))
        except OSError:
            self.service.logger.exception('场景列表读取失败')
            self.log('场景列表读取失败，请检查目录权限。')

    def select_scene(self, _event=None):
        selection = self.tree.selection()
        if selection:
            self.selected_scene.set(selection[0])

    def start(self, scene: bool):
        if self.busy:
            return
        path = self.scenes.get(self.selected_scene.get())
        if scene and path is None:
            messagebox.showwarning('选择场景', '请先生成或选择已有场景库。', parent=self.window)
            return
        self.log('启动场景系统' if scene else '启动系统')
        def monitor(process):
            stop_event = threading.Event()
            self.launch_stop_event = stop_event
            self.log('已提交启动请求，正在等待系统就绪。')
            def read():
                try:
                    for line in process.stdout:
                        self.events.put(('log', None, redact(line.rstrip())))
                    code = process.wait()
                    if code:
                        self.events.put(('launch_exit', None, (code, stop_event)))
                finally:
                    process.stdout.close()
            threading.Thread(target=read, daemon=True).start()
        self._task(lambda: self.service.start_scene(path) if scene else self.service.start_default(), monitor)

    def stop(self):
        if self.busy:
            return
        stop_event = self.launch_stop_event
        if stop_event is not None:
            stop_event.set()
        self.log('停止系统')
        def action():
            try:
                self.service.stop()
            except Exception:
                if stop_event is not None:
                    stop_event.clear()
                raise
        self._task(action, lambda _result: self.log('系统已停止'))

    def refresh_status(self):
        if self.closed:
            return
        if not self.status_pending:
            self.status_pending = True
            def work():
                try:
                    self.events.put(('status', None, self.service.status()))
                except Exception as exc:
                    self.service.logger.exception('状态读取失败')
                    self.events.put(('status_error', None, exc))
            threading.Thread(target=work, daemon=True).start()
    def _poll_status(self):
        if not self.closed:
            self.refresh_status()
            self.window.after(5000, self._poll_status)

    def _show_status(self, status):
        self.current_status = status
        self.status_text.set('运行中' if status['running'] else '未启动')
        ids = lambda key: ', '.join(map(str, status[key])) or '—'
        self.pid_text.set(f'Web PID：{ids("web_pids")}    Worker PID：{ids("worker_pids")}')
        self.database_text.set('端口被其他程序占用' if status['port_occupied'] else f'数据库：{status.get("db_path", "—")}')

    def close(self):
        if self.busy:
            messagebox.showinfo('操作进行中', '请等待当前操作完成后关闭。', parent=self.window)
            return
        if self.current_status.get('running') or self.current_status.get('worker_pids'):
            if not messagebox.askyesno('关闭管理工具', '关闭工具不会停止系统。是否继续关闭？', parent=self.window):
                return
        self.closed = True
        self.service.close()
        self.window.destroy()


def main():
    window = tk.Tk()
    ScenarioGui(window)
    window.mainloop()


if __name__ == '__main__':
    main()
