using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace AirportGroupLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new LauncherForm());
        }
    }

    internal sealed class DockerResult
    {
        public bool Success { get; private set; }
        public bool TimedOut { get; private set; }
        public string Error { get; private set; }

        public DockerResult(bool success, bool timedOut, string error)
        {
            Success = success;
            TimedOut = timedOut;
            Error = error ?? String.Empty;
        }
    }

    internal sealed class LauncherForm : Form
    {
        private const string SystemUrl = "http://127.0.0.1:8080";
        private const string HealthUrl = SystemUrl + "/login";
        private readonly string _deploymentRoot;
        private readonly string _composePath;
        private readonly Label _statusLabel;
        private readonly Button _openButton;
        private readonly Button _stopButton;
        private readonly Button _exitButton;
        private bool _busy;
        private bool _running;
        private bool _closing;
        private bool _automaticBrowserOpened;

        public LauncherForm()
        {
            _deploymentRoot = Path.GetDirectoryName(Application.ExecutablePath) ?? String.Empty;
            _composePath = Path.Combine(_deploymentRoot, "compose.yaml");

            Text = "机场群顽存能力模拟仿真验证系统";
            ClientSize = new Size(520, 220);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = true;
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Regular, GraphicsUnit.Point);

            Label titleLabel = new Label();
            titleLabel.Text = "机场群顽存能力模拟仿真验证系统";
            titleLabel.Font = new Font(Font.FontFamily, 14F, FontStyle.Bold);
            titleLabel.TextAlign = ContentAlignment.MiddleCenter;
            titleLabel.SetBounds(20, 20, 480, 36);

            _statusLabel = new Label();
            _statusLabel.Text = "系统状态：正在检测...";
            _statusLabel.TextAlign = ContentAlignment.MiddleCenter;
            _statusLabel.SetBounds(20, 68, 480, 40);

            _openButton = new Button();
            _openButton.Text = "打开系统";
            _openButton.SetBounds(65, 140, 120, 38);
            _openButton.Click += OpenButtonClick;

            _stopButton = new Button();
            _stopButton.Text = "关闭系统";
            _stopButton.SetBounds(200, 140, 120, 38);
            _stopButton.Click += StopButtonClick;

            _exitButton = new Button();
            _exitButton.Text = "退出";
            _exitButton.SetBounds(335, 140, 120, 38);
            _exitButton.Click += ExitButtonClick;

            Controls.Add(titleLabel);
            Controls.Add(_statusLabel);
            Controls.Add(_openButton);
            Controls.Add(_stopButton);
            Controls.Add(_exitButton);

            FormClosing += delegate { _closing = true; };
            Shown += async delegate { await EnsureSystemRunningAsync(true); };
            UpdateButtons();
        }

        private async void OpenButtonClick(object sender, EventArgs e)
        {
            await EnsureSystemRunningAsync(false);
        }

        private async void StopButtonClick(object sender, EventArgs e)
        {
            await StopSystemAsync();
        }

        private void ExitButtonClick(object sender, EventArgs e)
        {
            Close();
        }

        private async Task EnsureSystemRunningAsync(bool automaticOpen)
        {
            if (_busy || _closing)
            {
                return;
            }

            SetBusy(true);
            try
            {
                SetStatus("系统状态：正在检测...");
                if (!File.Exists(_composePath))
                {
                    SetRunning(false);
                    ShowSimpleError("未找到系统部署文件 compose.yaml。");
                    return;
                }

                if (await IsHealthyAsync())
                {
                    SetRunning(true);
                    OpenBrowser(automaticOpen);
                    return;
                }

                if (!await DockerEnvironmentAvailableAsync())
                {
                    SetRunning(false);
                    ShowSimpleError("未检测到可用的 Docker 运行环境，请先启动 Docker Desktop。");
                    return;
                }

                SetStatus("系统状态：正在启动...");
                DockerResult start = await RunDockerAsync("compose up -d", 60000);
                if (!start.Success)
                {
                    SetRunning(false);
                    SetStatus("系统状态：启动失败");
                    ShowSimpleError("系统未能正常启动，请检查 Docker 运行状态。" + ShortError(start));
                    return;
                }

                for (int attempt = 0; attempt < 60; attempt++)
                {
                    if (_closing)
                    {
                        return;
                    }
                    if (await IsHealthyAsync())
                    {
                        SetRunning(true);
                        OpenBrowser(automaticOpen);
                        return;
                    }
                    await Task.Delay(1000);
                }

                SetRunning(false);
                SetStatus("系统状态：启动失败");
                ShowSimpleError("系统未能正常启动，请检查 Docker 运行状态。");
            }
            finally
            {
                SetBusy(false);
            }
        }

        private async Task StopSystemAsync()
        {
            if (_busy || _closing || !_running)
            {
                return;
            }

            SetBusy(true);
            try
            {
                SetStatus("系统状态：正在关闭...");
                if (!File.Exists(_composePath))
                {
                    SetRunning(false);
                    ShowSimpleError("未找到系统部署文件 compose.yaml。");
                    return;
                }
                if (!await DockerEnvironmentAvailableAsync())
                {
                    SetRunning(false);
                    ShowSimpleError("未检测到可用的 Docker 运行环境，请先启动 Docker Desktop。");
                    return;
                }

                DockerResult stop = await RunDockerAsync("compose down", 60000);
                if (!stop.Success)
                {
                    SetRunning(await IsHealthyAsync());
                    ShowSimpleError("系统未能正常关闭，请检查 Docker 运行状态。" + ShortError(stop));
                    return;
                }

                SetRunning(false);
            }
            finally
            {
                SetBusy(false);
            }
        }

        private async Task<bool> DockerEnvironmentAvailableAsync()
        {
            DockerResult docker = await RunDockerAsync("--version", 10000);
            if (!docker.Success)
            {
                return false;
            }
            DockerResult compose = await RunDockerAsync("compose version", 10000);
            if (!compose.Success)
            {
                return false;
            }
            DockerResult engine = await RunDockerAsync("info --format \"{{.ServerVersion}}\"", 15000);
            return engine.Success;
        }

        private Task<bool> IsHealthyAsync()
        {
            return Task.Run(delegate
            {
                try
                {
                    HttpWebRequest request = (HttpWebRequest)WebRequest.Create(HealthUrl);
                    request.Method = "GET";
                    request.Timeout = 2000;
                    request.ReadWriteTimeout = 2000;
                    request.AllowAutoRedirect = true;
                    using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                    {
                        int status = (int)response.StatusCode;
                        return status >= 200 && status < 400;
                    }
                }
                catch
                {
                    return false;
                }
            });
        }

        private Task<DockerResult> RunDockerAsync(string arguments, int timeoutMilliseconds)
        {
            return Task.Run(delegate
            {
                StringBuilder standardError = new StringBuilder();
                try
                {
                    ProcessStartInfo startInfo = new ProcessStartInfo();
                    startInfo.FileName = "docker";
                    startInfo.Arguments = arguments;
                    startInfo.WorkingDirectory = _deploymentRoot;
                    startInfo.UseShellExecute = false;
                    startInfo.CreateNoWindow = true;
                    startInfo.WindowStyle = ProcessWindowStyle.Hidden;
                    startInfo.RedirectStandardOutput = true;
                    startInfo.RedirectStandardError = true;
                    ApplyLocalSecret(startInfo);

                    using (Process process = new Process())
                    {
                        process.StartInfo = startInfo;
                        process.OutputDataReceived += delegate { };
                        process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                        {
                            if (!String.IsNullOrWhiteSpace(e.Data) && standardError.Length < 1000)
                            {
                                standardError.AppendLine(e.Data);
                            }
                        };
                        process.Start();
                        process.BeginOutputReadLine();
                        process.BeginErrorReadLine();
                        if (!process.WaitForExit(timeoutMilliseconds))
                        {
                            try { process.Kill(); } catch { }
                            return new DockerResult(false, true, "");
                        }
                        process.WaitForExit();
                        return new DockerResult(process.ExitCode == 0, false, standardError.ToString());
                    }
                }
                catch (Win32Exception)
                {
                    return new DockerResult(false, false, "");
                }
                catch (Exception error)
                {
                    return new DockerResult(false, false, error.Message);
                }
            });
        }

        private void ApplyLocalSecret(ProcessStartInfo startInfo)
        {
            string current = Environment.GetEnvironmentVariable("AIRPORT_GROUP_SECRET_KEY");
            if (!String.IsNullOrWhiteSpace(current))
            {
                return;
            }

            string localSettings = Path.Combine(_deploymentRoot, ".env.local.ps1");
            if (!File.Exists(localSettings))
            {
                return;
            }

            const string key = "$env:AIRPORT_GROUP_SECRET_KEY";
            foreach (string rawLine in File.ReadAllLines(localSettings, Encoding.UTF8))
            {
                string line = rawLine.Trim();
                if (!line.StartsWith(key, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                int equals = line.IndexOf('=');
                if (equals < 0)
                {
                    continue;
                }
                string value = line.Substring(equals + 1).Trim();
                if (value.Length >= 2 &&
                    ((value[0] == '\'' && value[value.Length - 1] == '\'') ||
                     (value[0] == '"' && value[value.Length - 1] == '"')))
                {
                    value = value.Substring(1, value.Length - 2);
                }
                if (value.Length >= 32)
                {
                    startInfo.EnvironmentVariables["AIRPORT_GROUP_SECRET_KEY"] = value;
                }
                return;
            }
        }

        private void OpenBrowser(bool automaticOpen)
        {
            if (automaticOpen && _automaticBrowserOpened)
            {
                return;
            }
            if (automaticOpen)
            {
                _automaticBrowserOpened = true;
            }
            try
            {
                ProcessStartInfo browser = new ProcessStartInfo();
                browser.FileName = SystemUrl;
                browser.UseShellExecute = true;
                Process.Start(browser);
            }
            catch
            {
                ShowSimpleError("无法打开默认浏览器，请手动访问 " + SystemUrl + "。");
            }
        }

        private static string ShortError(DockerResult result)
        {
            if (result.TimedOut)
            {
                return "\nDocker 操作超时。";
            }
            string error = (result.Error ?? String.Empty).Trim();
            if (error.Length == 0)
            {
                return String.Empty;
            }
            if (error.Length > 260)
            {
                error = error.Substring(0, 260) + "...";
            }
            return "\n" + error;
        }

        private void SetBusy(bool busy)
        {
            _busy = busy;
            UpdateButtons();
        }

        private void SetRunning(bool running)
        {
            _running = running;
            SetStatus(running ? "系统状态：运行中" : "系统状态：未运行");
            UpdateButtons();
        }

        private void SetStatus(string status)
        {
            if (!_closing && !IsDisposed)
            {
                _statusLabel.Text = status;
            }
        }

        private void UpdateButtons()
        {
            _openButton.Enabled = !_busy;
            _stopButton.Enabled = !_busy && _running;
            _exitButton.Enabled = true;
        }

        private void ShowSimpleError(string message)
        {
            if (!_closing && !IsDisposed)
            {
                MessageBox.Show(this, message, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }
    }
}
