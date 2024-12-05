import fnmatch
import os
import subprocess
import re
import paramiko
from abc import ABC, abstractmethod


# Абстракция для работы с пакетами
class PackageManager(ABC):
    @abstractmethod
    def simulate_install_package(self, package_name, output_file):
        pass

    @abstractmethod
    def generate_name_depend_package(self, strings):
        pass


class APTPackageManager(PackageManager):
    def simulate_install_package(self, package_name, output_file):
        try:
            result = subprocess.run(
                ['sudo', 'apt', 'install', '-s', package_name],
                capture_output=True,
                text=True,
                check=True
            )
            filtered_output = [line for line in result.stdout.splitlines() if line.startswith('Inst ')]

            with open(output_file, 'w') as file:
                for line in filtered_output:
                    file.write("\n" + line)

            print(f"Успешная симуляция установки для {package_name}")

        except subprocess.CalledProcessError as e:
            print(f"Пакет: {package_name}\n"
                  f"Ошибка при симуляции установки: {e.stderr.decode()}")

    def generate_name_depend_package(self, strings):
        for string in strings:
            if string.startswith("Inst"):
                match = re.search(r'Inst (\S+).*?\((\S+)', string)
                yield f"{match.group(1)}={match.group(2)}" if match else None


# Абстракция для работы с репозиториями Git
class GitRepository(ABC):
    @abstractmethod
    def clone(self, repo_url, dest_path=''):
        pass


class GitClient(GitRepository):
    def clone(self, repo_url, dest_path=''):
        try:
            result = subprocess.run(
                ["git", "clone", repo_url, dest_path],
                check=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            print("Репозиторий успешно склонирован")
        except subprocess.CalledProcessError as e:
            print("Ошибка при клонировании репозитория: ", e.stderr.decode())


# Абстракция для работы с файловой системой
class FileSystem(ABC):
    @abstractmethod
    def search_main_packages(self, dir_path, save_to, os_name=None, release_name=None, module_name=None, level=1):
        pass


class LocalFileSystem(FileSystem):
    def search_main_packages(self, dir_path, save_to, os_name=None, release_name=None, module_name=None, level=1):
        global listPackages
        package_list_path = os.path.join(dir_path, 'package.list')
        if os.path.isfile(package_list_path):
            print(f"Найден файл: {package_list_path}")
            with open(package_list_path, 'r') as packages_txt:
                listPackages += packages_txt.read().splitlines()

        # Обход подкаталогов
        for sub_dir in os.listdir(dir_path):
            sub_dir_path = os.path.join(dir_path, sub_dir)
            if os.path.isdir(sub_dir_path):
                if level == 1 and module_name and not fnmatch.fnmatch(sub_dir, module_name):
                    continue
                if level == 2 and os_name and not fnmatch.fnmatch(sub_dir, os_name):
                    continue
                if level == 3 and release_name and not fnmatch.fnmatch(sub_dir, release_name):
                    continue
                self.search_main_packages(sub_dir_path, save_to, os_name, release_name, module_name, level + 1)


# Абстракция для работы с SSH
class SSHConnectionHandler(ABC):
    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def run_command(self, command):
        pass

    @abstractmethod
    def close(self):
        pass


class SSHConnection(SSHConnectionHandler):
    def __init__(self, hostname, username, password):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.sftp = None
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self):
        try:
            self.client.connect(self.hostname, username=self.username, password=self.password)
            print(f"Подключение к {self.hostname} установлено.")
        except Exception as e:
            print(f"Ошибка при подключении: {e}")
            exit(1)

    def run_command(self, command):
        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            return stdout.readlines(), stderr.readlines()
        except Exception as e:
            print(f"Ошибка при выполнении команды: {e}")
            return [], []

    def close(self):
        self.client.close()

    def sftp_open(self):
        self.sftp = self.client.open_sftp()

    def copy_from_remote(self, remote_path, local_path):
        self.sftp.get(remote_path, local_path)


class PackageDownloader:
    def __init__(self, package_manager: PackageManager, ssh_connection: SSHConnectionHandler):
        self.package_manager = package_manager
        self.ssh_connection = ssh_connection

    def download_dependencies(self, listPackages, os_name):
        for package in set(listPackages):
            print(f"\nРаботаю с : {package}...")
            stdout, stderr = self.ssh_connection.run_command(f"apt install -s {package}")
            for downloadPackage in self.package_manager.generate_name_depend_package(stdout):
                print(f"Скачиваю зависимость: {downloadPackage}...")
                self.ssh_connection.run_command(f"cd depends && apt download {downloadPackage}")


def main():
    os_name = "Ubuntu"
    other = ""
    archive_name = f"depends_{os_name}_{other}.tar.gz"
    listPackages = []
    dirPath = os.getcwd()

    # Создание объектов
    git_client = GitClient()
    git_client.clone('***', dirPath + "/distrib_dep")

    file_system = LocalFileSystem()
    file_system.search_main_packages(dirPath + "/distrib_dep/linux", dirPath + "/packages.txt", os_name)

    ssh_connection = SSHConnection("akvs3-u18-moskvitin", "echelon", "seclab")
    ssh_connection.connect()
    ssh_connection.run_command(f"rm -rf depends depends_{os_name}.tar.gz && mkdir -p depends")

    package_manager = APTPackageManager()
    package_downloader = PackageDownloader(package_manager, ssh_connection)
    package_downloader.download_dependencies(listPackages, os_name)

    ssh_connection.run_command(f"tar -czvf {archive_name} ~/depends")
    ssh_connection.sftp_open()
    ssh_connection.copy_from_remote(f"/home/echelon/{archive_name}", f"{dirPath}/{archive_name}")
    ssh_connection.close()


if __name__ == "__main__":
    main()
