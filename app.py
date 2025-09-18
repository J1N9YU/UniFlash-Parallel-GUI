import subprocess
import os
import re
import time
import threading
import shutil
import zlib
from datetime import datetime
from xml.etree import ElementTree as ET
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import webbrowser

app = Flask(__name__)
CORS(app)

# ======================
# 配置参数 - 请根据实际情况修改以下内容
# ======================

# dslite.exe的路径
DSLITE_PATH = r"C:\ti\uniflash_9.2.0\deskdb\content\TICloudAgent\win\ccs_base\DebugServer\bin\dslite.exe"

# 设备扫描工具路径
XDSDFU_PATH = r"C:\ti\uniflash_9.2.0\deskdb\content\TICloudAgent\win\ccs_base\common\uscif\xds110\xdsdfu.exe"

# 母版ccxml文件路径（用于复制和修改）
MASTER_CCXML_PATH = r"TMS320F28P550SJ9_LaunchPad.ccxml"

# 生成的ccxml文件存放目录（自动创建）
GENERATED_CCXML_DIR = r"generated_ccxml"

# 烧录文件存放目录
IMAGE_DIR = r"image"

# 要烧录的二进制文件路径（所有通道共用）
OUT_FILE = os.path.join(IMAGE_DIR, "led_ex1_blinky_green.out")

# 初始通道数量
NUM_CHANNELS = 1

# 最大烧录次数限制（默认3次）
MAX_FLASH_COUNT = 3

# ======================
# 全局状态管理
# ======================

# 各通道使用的ccxml文件（动态生成）
CCXML_FILES = ["" for _ in range(8)]  # 索引0对应通道1，以此类推

# 烧录状态：未开始、烧录中、烧录成功、烧录失败
channel_status = {i: "未开始" for i in range(1, 9)}

# 烧录计数
success_count = {i: 0 for i in range(1, 9)}
fail_count = {i: 0 for i in range(1, 9)}

# 烧录进程
processes = {}

# 是否有烧录在进行中
is_running = False

# 设备序列号
device_serials = []

# 线程锁
status_lock = threading.Lock()

# ======================
# 初始化操作
# ======================

def init_generated_dir():
    """初始化生成ccxml文件的目录"""
    if not os.path.exists(GENERATED_CCXML_DIR):
        os.makedirs(GENERATED_CCXML_DIR)
        app.logger.info(f"创建ccxml生成目录: {GENERATED_CCXML_DIR}")
    else:
        # 清理目录中已有的ccxml文件
        for file in os.listdir(GENERATED_CCXML_DIR):
            if file.endswith(".ccxml"):
                try:
                    os.remove(os.path.join(GENERATED_CCXML_DIR, file))
                except Exception as e:
                    app.logger.warning(f"清理旧ccxml文件失败: {str(e)}")

# 初始化生成目录
init_generated_dir()

# 确保image目录存在
if not os.path.exists(IMAGE_DIR):
    os.makedirs(IMAGE_DIR)
    app.logger.info(f"创建image目录: {IMAGE_DIR}")

# ======================
# CRC32计算函数
# ======================

def calculate_file_crc32(file_path):
    """计算文件的CRC32校验码"""
    if not os.path.exists(file_path):
        app.logger.error(f"文件不存在，无法计算CRC32: {file_path}")
        return None
    
    crc = 0
    with open(file_path, 'rb') as f:
        while True:
            data = f.read(65536)  # 64KB块读取
            if not data:
                break
            crc = zlib.crc32(data, crc)
    
    # 返回8位十六进制字符串
    return f"{crc & 0xFFFFFFFF:08X}"

# ======================
# CCXML文件处理函数
# ======================

def create_ccxml_with_serial(channel, serial):
    """
    根据母版ccxml文件创建带有指定序列号的新文件（文本替换版）
    """
    if not os.path.exists(MASTER_CCXML_PATH):
        return False, f"母版ccxml文件不存在: {MASTER_CCXML_PATH}"
    
    new_filename = f"channel_{channel}_serial_{serial}.ccxml"
    new_filepath = os.path.join(GENERATED_CCXML_DIR, new_filename)
    
    try:
        # 读取母版文件内容
        with open(MASTER_CCXML_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 正则匹配模式：匹配 id="-- Enter the serial number" 对应的 Value 属性
        pattern = r'(Value=["\']).*?(["\']\s+id=["\']-- Enter the serial number["\'])'
        
        # 执行替换
        new_content, replace_count = re.subn(
            pattern,
            lambda m: f'{m.group(1)}{serial}{m.group(2)}',
            content
        )
        
        if replace_count == 0:
            return False, "未找到需要替换的序列号位置（id=\"-- Enter the serial number\"）"
        
        # 写入新文件
        with open(new_filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        app.logger.info(f"已生成通道 {channel} 的ccxml文件: {new_filepath} (序列号: {serial})")
        return True, new_filepath
        
    except Exception as e:
        if os.path.exists(new_filepath):
            os.remove(new_filepath)
        return False, f"生成ccxml文件失败: {str(e)}"

# ======================
# 密码相关函数
# ======================

def read_encryption_passwords(file_path="password.txt"):
    """读取加密密码文件中的8个-s参数值"""
    try:
        with open(file_path, 'r') as f:
            passwords = [line.strip() for line in f if line.strip()]
        
        if len(passwords) != 8:
            raise ValueError(f"密码文件应包含8个参数，实际读取到{len(passwords)}个")
        return passwords
    except FileNotFoundError:
        raise FileNotFoundError(f"未找到密码文件: {file_path}")
    except Exception as e:
        raise Exception(f"读取密码文件失败: {str(e)}")

# ======================
# 烧录相关函数
# ======================

def _check_success_flag(output_file):
    """检查输出文件末尾三行，判断烧录是否成功"""
    try:
        with open(output_file, 'r', encoding='utf-8', errors='ignore') as f:
            # 读取文件所有行，获取末尾三行
            lines = f.readlines()
            # 取最后三行（如果不足三行则取全部）
            last_three_lines = lines[-3:] if len(lines) >= 3 else lines
            # 拼接成字符串并转为小写，便于判断
            content = ' '.join(last_three_lines).lower()
            
            # 关键失败标志：芯片上锁时的错误信息
            failure_flags = [
                "failed: unknown error",
                "device is locked"  # 其他可能的锁定位符
            ]
            
            # 判断逻辑：必须包含success且不包含任何失败标志
            has_success = "success" in content
            has_failure = any(flag in content for flag in failure_flags)
            
            return has_success and not has_failure
            
    except Exception as e:
        app.logger.error(f"检查成功标志时发生错误: {str(e)}")
        return False

def generate_burn_command(ccxml_file, is_encryption_enabled=True):
    """
    生成烧录命令
    :param config_file: 配置文件路径
    :param settings_file: 设置文件路径
    :param is_encryption_enabled: 加密开关状态
    :return: 完整烧录命令字符串
    """
    # 基础命令部分
    command_parts = [
        DSLITE_PATH,
        "flash",
        "-c", ccxml_file,
        "-e", "-f", "-v",
        OUT_FILE
    ]
    
    # 如果开启加密，添加额外参数
    if is_encryption_enabled:
        # 添加固定的-a参数
        command_parts.append("-s VerifyAfterProgramLoad=\"No verification\"")
        command_parts.append("-s FlashResetOnOperation=false")
        
        command_parts.append("-b Z1Unlock")
        # command_parts.append("-b Z1AllProgram")
        command_parts.append("-a Z1Unlock")
        command_parts.append("-a Z1PasswordProgram")
        command_parts.append("-a Z1GRABEXEONLYProgram")
        
        
        # 读取并添加-s参数
        encryption_params = read_encryption_passwords()
        for param in encryption_params:
            command_parts.append(f"-s {param}")
    
    # 拼接成完整命令
    return ' '.join(command_parts)

def _run_dslite(channel, encryption_enabled):
    """运行单个通道的烧录进程"""
    global is_running, channel_status, success_count, fail_count
    
    # 检查通道对应的ccxml文件是否存在
    ccxml_file = CCXML_FILES[channel - 1]  # CCXML_FILES索引0对应通道1
    if not ccxml_file or not os.path.exists(ccxml_file):
        with status_lock:
            channel_status[channel] = "配置错误: 未找到ccxml文件"
        app.logger.error(f"通道 {channel} 未找到ccxml文件: {ccxml_file}")
        return
    
    output_file = f"flash_channel_{channel}.txt"
    
    try:
        # 更新状态
        with status_lock:
            channel_status[channel] = "烧录中"
            is_running = True
        
        # 构建命令
        cmd = generate_burn_command(ccxml_file, encryption_enabled)
        
        app.logger.info(f"通道 {channel} 执行命令: {cmd}")
        
        # 执行命令
        start_time = datetime.now()
        with open(output_file, 'w', encoding='utf-8') as f:
            process = subprocess.run(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300  # 5分钟超时
            )
        
        # 检查结果
        success = _check_success_flag(output_file)
        duration = (datetime.now() - start_time).total_seconds()
        
        # 更新状态和计数
        with status_lock:
            if success:
                channel_status[channel] = "烧录成功"
                success_count[channel] += 1
            else:
                channel_status[channel] = f"烧录失败 (返回码: {process.returncode})"
                fail_count[channel] += 1
            
            # 检查是否还有运行中的通道
            is_running = any(status == "烧录中" for status in channel_status.values())
            
    except Exception as e:
        app.logger.error(f"通道 {channel} 烧录错误: {str(e)}")
        with status_lock:
            channel_status[channel] = f"烧录错误: {str(e)}"
            fail_count[channel] += 1
            is_running = any(status == "烧录中" for status in channel_status.values())

def start_single_channel(channel, encryption_enabled):
    """启动单个通道的烧录"""
    global processes, MAX_FLASH_COUNT
    
    # 检查是否超过最大烧录次数
    total_success = sum(success_count.values())
    if total_success >= MAX_FLASH_COUNT:
        return False, f"已达到最大烧录次数({MAX_FLASH_COUNT}次)，无法继续烧录"
    
    with status_lock:
        if channel_status[channel] == "烧录中":
            return False, "该通道正在烧录中"
    
    # 创建并启动线程
    thread = threading.Thread(target=_run_dslite, args=(channel, encryption_enabled), daemon=True)
    thread.start()
    processes[channel] = thread
    return True, f"通道 {channel} 烧录已启动"

def start_all_channels(num_channels, encryption_enabled):
    """启动所有通道的烧录"""
    global is_running, MAX_FLASH_COUNT
    
    # 检查是否超过最大烧录次数
    total_success = sum(success_count.values())
    if total_success >= MAX_FLASH_COUNT:
        return False, f"已达到最大烧录次数({MAX_FLASH_COUNT}次)，无法继续烧录"
    
    with status_lock:
        if is_running:
            return False, "已有烧录任务在进行中"
    
    # 启动每个通道
    for channel in range(1, num_channels + 1):
        with status_lock:
            if channel_status[channel] != "烧录中":
                thread = threading.Thread(target=_run_dslite, args=(channel, encryption_enabled), daemon=True)
                thread.start()
                processes[channel] = thread
                time.sleep(0.2)  # 稍微延迟，避免同时启动冲突
    
    return True, f"所有 {num_channels} 个通道烧录已启动"

def stop_all_channels():
    """停止所有通道的烧录"""
    global is_running, channel_status
    
    # 标记状态为终止
    with status_lock:
        for channel in processes:
            if channel_status[channel] == "烧录中":
                channel_status[channel] = "烧录终止"
                fail_count[channel] += 1
        
        is_running = False
    
    return True, "所有烧录任务已终止"

def reset_counters():
    """重置所有烧录计数"""
    global success_count, fail_count
    with status_lock:
        success_count = {i: 0 for i in range(1, 9)}
        fail_count = {i: 0 for i in range(1, 9)}
    return True, "烧录计数已重置"

# ======================
# 设备扫描相关函数
# ======================

def scan_devices():
    """扫描连接的烧录器设备并生成对应的ccxml文件"""
    global device_serials, CCXML_FILES, NUM_CHANNELS
    
    try:
        # 检查xdsdfu是否存在
        if not os.path.exists(XDSDFU_PATH):
            return False, [], f"未找到设备扫描工具: {XDSDFU_PATH}"
        
        # 执行扫描命令
        result = subprocess.run(
            [XDSDFU_PATH, "-e"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        output = result.stdout + result.stderr
        
        # 解析输出，提取序列号
        serials = []
        pattern = r"Serial Num:\s+([A-Za-z0-9]+)"  # 匹配序列号格式
        matches = re.findall(pattern, output)
        
        if not matches:
            device_serials = []
            return True, [], "未找到任何烧录器设备"
        
        # 限制最大数量为8
        max_devices = min(len(matches), 8)
        serials = matches[:max_devices]
        device_serials = serials
        
        # 为每个设备生成对应的ccxml文件
        success_channels = 0
        for i in range(max_devices):
            channel = i + 1  # 通道号从1开始
            serial = serials[i]
            
            # 生成带有序列号的ccxml文件
            success, msg = create_ccxml_with_serial(channel, serial)
            if success:
                CCXML_FILES[i] = msg  # 更新通道对应的ccxml文件路径
                success_channels += 1
                app.logger.info(f"通道 {channel} 已关联序列号: {serial}")
            else:
                app.logger.error(f"通道 {channel} 关联序列号失败: {msg}")
        
        # 更新通道数量为成功关联的设备数
        if success_channels > 0:
            NUM_CHANNELS = success_channels
        
        return True, serials, f"成功找到 {len(serials)} 个设备，其中 {success_channels} 个已配置ccxml文件"
        
    except Exception as e:
        app.logger.error(f"设备扫描错误: {str(e)}")
        return False, [], f"扫描设备时发生错误: {str(e)}"

# ======================
# Flask路由
# ======================

@app.route('/')
def index():
    """前端页面"""
    return render_template('index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置信息"""
    # 计算OUT文件的CRC32
    crc32 = calculate_file_crc32(OUT_FILE)
    
    return jsonify({
        "num_channels": NUM_CHANNELS,
        "dslite_path": DSLITE_PATH,
        "out_file": OUT_FILE,
        "out_file_crc32": crc32,
        "max_flash_count": MAX_FLASH_COUNT,
        "ccxml_files": [CCXML_FILES[i] for i in range(NUM_CHANNELS)],
        "master_ccxml": MASTER_CCXML_PATH
    })

@app.route('/api/config', methods=['POST'])
def update_config():
    """更新配置信息"""
    global NUM_CHANNELS, MAX_FLASH_COUNT
    data = request.json
    
    if "num_channels" in data:
        new_num = int(data["num_channels"])
        if 1 <= new_num <= 8:
            NUM_CHANNELS = new_num
            return jsonify({
                "status": "success",
                "message": f"通道数已更新为 {NUM_CHANNELS}"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "通道数必须在1-8之间"
            })
    
    if "max_flash_count" in data:
        new_max = int(data["max_flash_count"])
        if 1 <= new_max <= 100:
            MAX_FLASH_COUNT = new_max
            return jsonify({
                "status": "success",
                "message": f"最大烧录次数已更新为 {MAX_FLASH_COUNT}"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "最大烧录次数必须在1-100之间"
            })
    
    return jsonify({
        "status": "error",
        "message": "无效的配置参数"
    })

@app.route('/api/status', methods=['GET'])
def get_status():
    """获取当前状态"""
    with status_lock:
        total_success = sum(success_count.values())
        total_fail = sum(fail_count.values())
        
        return jsonify({
            "is_running": is_running,
            "num_channels": NUM_CHANNELS,
            "max_flash_count": MAX_FLASH_COUNT,
            "total_success": total_success,
            "total_fail": total_fail,
            "channels": {k: v for k, v in channel_status.items() if k <= NUM_CHANNELS},
            "counters": {
                "success": {k: v for k, v in success_count.items() if k <= NUM_CHANNELS},
                "fail": {k: v for k, v in fail_count.items() if k <= NUM_CHANNELS}
            },
            "serials": device_serials,
            "ccxml_files": {i+1: CCXML_FILES[i] for i in range(NUM_CHANNELS)}  # 通道号对应ccxml文件
        })

@app.route('/api/start', methods=['POST'])
def start_flash():
    """开始所有通道烧录"""
    data = request.json
    encryption_enabled = data.get('encryptionEnabled', False)
    success, message = start_all_channels(NUM_CHANNELS, encryption_enabled)
    return jsonify({
        "status": "success" if success else "error",
        "message": message
    })

@app.route('/api/start/<int:channel>', methods=['POST'])
def start_single_flash(channel):
    """开始单个通道烧录"""
    if 1 <= channel <= NUM_CHANNELS:
        data = request.json
        encryption_enabled = data.get('encryptionEnabled', False)
        success, message = start_single_channel(channel, encryption_enabled)
        return jsonify({
            "status": "success" if success else "error",
            "message": message
        })
    else:
        return jsonify({
            "status": "error",
            "message": f"无效的通道号: {channel}"
        })

@app.route('/api/stop', methods=['POST'])
def stop_flash():
    """停止所有通道烧录"""
    success, message = stop_all_channels()
    return jsonify({
        "status": "success" if success else "error",
        "message": message
    })

@app.route('/api/reset', methods=['POST'])
def reset_counts():
    """重置烧录计数"""
    success, message = reset_counters()
    return jsonify({
        "status": "success" if success else "error",
        "message": message
    })

@app.route('/api/scan', methods=['POST'])
def scan():
    """扫描设备并生成ccxml文件"""
    success, serials, message = scan_devices()
    return jsonify({
        "status": "success" if success else "error",
        "message": message,
        "serials": serials,
        "count": len(serials),
        "configured_count": sum(1 for i in range(len(serials)) if CCXML_FILES[i] and os.path.exists(CCXML_FILES[i]))
    })

# 新增：获取image文件夹内的文件列表
@app.route('/api/image_files', methods=['GET'])
def get_image_files():
    """获取image目录下的所有.out文件列表"""
    try:
        if not os.path.exists(IMAGE_DIR):
            return jsonify({
                "status": "error",
                "message": f"image目录不存在: {IMAGE_DIR}"
            })
        
        # 获取目录下所有.out文件
        out_files = []
        for file in os.listdir(IMAGE_DIR):
            if file.endswith(".out") and os.path.isfile(os.path.join(IMAGE_DIR, file)):
                out_files.append(file)
        
        return jsonify({
            "status": "success",
            "files": out_files,
            "current_file": os.path.basename(OUT_FILE)
        })
    except Exception as e:
        app.logger.error(f"获取image文件列表错误: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"获取文件列表失败: {str(e)}"
        })

# 新增：更新选中的烧录文件
@app.route('/api/set_image_file', methods=['POST'])
def set_image_file():
    """设置选中的烧录文件"""
    global OUT_FILE
    data = request.json
    
    if not data or "filename" not in data:
        return jsonify({
            "status": "error",
            "message": "请提供文件名"
        })
    
    filename = data["filename"]
    new_path = os.path.join(IMAGE_DIR, filename)
    
    if not os.path.exists(new_path) or not os.path.isfile(new_path):
        return jsonify({
            "status": "error",
            "message": f"文件不存在: {filename}"
        })
    
    if not filename.endswith(".out"):
        return jsonify({
            "status": "error",
            "message": "请选择.out格式的文件"
        })
    
    # 更新全局变量
    OUT_FILE = new_path
    app.logger.info(f"已更新烧录文件为: {OUT_FILE}")
    
    # 计算新文件的CRC32
    crc32 = calculate_file_crc32(OUT_FILE)
    
    return jsonify({
        "status": "success",
        "message": f"已选择烧录文件: {filename}",
        "current_file": filename,
        "out_file_crc32": crc32
    })

# ======================
# 程序入口
# ======================

if __name__ == '__main__':
    # 验证关键文件是否存在
    if not os.path.exists(DSLITE_PATH):
        app.logger.warning(f"dslite.exe 未找到: {DSLITE_PATH}")
    
    if not os.path.exists(OUT_FILE):
        app.logger.warning(f"烧录文件未找到: {OUT_FILE}")
    
    if not os.path.exists(MASTER_CCXML_PATH):
        app.logger.error(f"母版ccxml文件不存在: {MASTER_CCXML_PATH} - 请检查配置")
    
    if not os.path.exists(XDSDFU_PATH):
        app.logger.warning(f"xdsdfu.exe 未找到: {XDSDFU_PATH}")
    
    # 新增：自动打开浏览器（延迟1秒，确保服务已启动）
    def open_browser():
        time.sleep(1)  # 等待服务启动
        webbrowser.open("http://localhost:5000")  # 打开Flask服务地址
    
    # 启动一个线程执行打开浏览器的操作（避免阻塞服务启动）
    threading.Thread(target=open_browser, daemon=True).start()     
    app.run(host='0.0.0.0', port=5000, debug=False)