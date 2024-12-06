import fnmatch
import os
import subprocess
import re
import argparse
from typing import Dict

import paramiko
from abc import ABC, abstractmethod


class Arguments:
    def __init__(self):
        description = """
        Скрипт для сборки локального репозитория для укзанной ОС с помощью пакета reprepro
        """
        epilog = """
        При запуске скрипта, необходимо указать для какой ОС собирать пакеты и данные для ВМ на которой будут собираться пакеты
        --release-name      -   необходимо указать для Debian и Ubuntu ( Например: bionic )
        --note              -   указывается для более удобного названия полученных файлов ( Например, 18 . Тогда получим: depends_ubuntu18.tar.gz )
        --git_link          -   указан поумолчанию, изменение не требуется
        
        ВАЖНО! Указанная ОС и ВМ должны совпадать.
        
        На основной машине должен быть установлен reprepro пакет. Выполни --prepare-repo
        """
        self.parser = argparse.ArgumentParser(description=description, epilog=epilog)
        self.init_args()

    def init_args(self):
        self.parser.add_argument('--os_name', type=str, required=True, help='Для какой ОС собирать зависимости?')
        self.parser.add_argument('--release_name', type=str, required=False, help='Название имени реализа ОС')
        self.parser.add_argument('--note', type=str, required=False,
                                 help='Версия ОС, которая будет указана в названии файла')
        self.parser.add_argument('--hostname', type=str, required=True,
                                 help='Хост ВМ, на которой будет произведена сборка')
        self.parser.add_argument('--username', type=str, required=True, help='Логин для ВМ')
        self.parser.add_argument('--password', type=str, required=True, help='Парол для ВМ')
        self.parser.add_argument('--git_link', type=str, required=False,
                                 default='git@gitlab-lest.ru:kvs/distrib_dep.git',
                                 help='Ссылка на репозиторий distrib_dep')
        self.parser.add_argument('--prepare-repo', type=str, required=False, help='Установит reprepro пакет')

    def get_args(self):
        return self.parser.parse_args()


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
            subprocess.run(
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
    def __init__(self):
        self.listPackages = []

    def search_main_packages(self, dir_path, save_to, os_name=None, release_name=None, module_name=None, level=1):
        package_list_path = os.path.join(dir_path, 'package.list')
        if os.path.isfile(package_list_path):
            print(f"Найден файл: {package_list_path}")
            with open(package_list_path, 'r') as packages_txt:
                self.listPackages += packages_txt.read().splitlines()

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

    @staticmethod
    def wrie_info_packages(packages_info: Dict[str, any], saved_file) -> None:
        with open(saved_file, "w") as data_file:
            for package_name in packages_info.keys():
                data_file.write(packages_info[package_name])
            data_file.close()
        print('Сохранил информацию о пакетах в ' + saved_file)

    @staticmethod
    def unpack_archive(worked_dir, path_to_archive):
        try:
            subprocess.run(["cd", worked_dir, "&&",
                            "tar", "-szvf", path_to_archive],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print("Архив из ВМ разархивирован")
        except subprocess.CalledProcessError as e:
            print("Ошибка при распаковке архива с пакетами: ", e)


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
        print(f"Начинаю копировать из удаленной ВМ..."
              f"Удаленный путь: {remote_path}"
              f"Сохраняю в {local_path}"
              f"\n")
        if os.path.exists(local_path):
            os.remove(local_path)
        self.sftp.get(remote_path, local_path)


class PackageDownloader:
    def __init__(self, package_manager: PackageManager, ssh_connection: SSHConnectionHandler):
        self.package_manager = package_manager
        self.ssh_connection = ssh_connection
        self.information_packages = {}

    def download_dependencies(self, list_packages):
        for package in set(list_packages):
            if "dotnet-sdk-5.0" == package:
                print("2")
            print(f"\nРаботаю с : {package}...")
            stdout, stderr = self.ssh_connection.run_command(f"apt install -s {package}")
            for downloadPackage in self.package_manager.generate_name_depend_package(stdout):
                print(f"Скачиваю зависимость: {downloadPackage}...")
                info_package, info_err = self.ssh_connection.run_command(
                    f"cd depends && apt download {downloadPackage}")
                if info_package and len(stderr) <= 3:
                    parts_str = info_package[0].split()
                    md5_value, stderr = self.ssh_connection.run_command(
                        f"md5sum depends/{'_'.join([parts_str[4], parts_str[6].replace(':', '%3a'), parts_str[5]])}.deb")
                    self.information_packages[downloadPackage] = ' | '.join([
                        parts_str[1],
                        parts_str[2],
                        parts_str[5],
                        parts_str[6],
                        md5_value[0],
                    ])


class BuildRepository:
    def __init__(self, name_repo, path_to_depends, worked_dir):
        self.name_repo = name_repo
        self.depends_catalog = path_to_depends
        self.repository_catalog = f"{worked_dir}/repo"  # Каталог для промежуточного сохранения пакетов и конвертации в локальный репозиторий
        self.config_content = """Codename: stable
        Suite: stable
        Components: main
        Architectures: amd64 i386"""

    def _create_repository_catalog(self):
        try:
            os.makedirs(f"{self.repository_catalog}/conf")
            os.makedirs(f"{self.repository_catalog}/incoming")
            print("Успешно создал каталог для локального репозитория")
        except OSError as e:
            print("Ошибка при создании каталога для репозитория: ", e)

    def _create_distributions_file(self):
        try:
            with open(f"{self.repository_catalog}/conf/distributions", "w") as config_file:
                config_file.write(self.config_content)
                config_file.close()
            print("Успешно создал конфиг. файл для репозитория")
        except IOError as e:
            print(f"Ошибка при создании конфиг. файла: ", e)

    def _added_packages(self):
        try:
            subprocess.run(["cd", self.repository_catalog, "&&",
                            "reprepro", "includedeb", "stable"
                                                      f"{self.depends_catalog}/*.deb"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print("В репозиторий успешно добавлены пакеты")
        except subprocess.CalledProcessError as e:
            print("Ошибка при добавлении пакетов в репозиторий: ", e)

    def _packing_repository(self):
        try:
            subprocess.run(["cd", os.path.dirname(self.depends_catalog), "&&",
                            "tar", "-czvf", os.path.basename(self.repository_catalog), self.name_repo],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print("Успешно создан архив:", os.path.abspath("localrepo"))
        except subprocess.CalledProcessError as e:
            print("Ошибка при архивировании: ", e)
        else:
            os.remove(self.repository_catalog)

    def initial_repo(self):
        print("\nНачинаю инициализацию репозитория...")
        self._create_repository_catalog()
        self._create_distributions_file()
        self._added_packages()
        self._packing_repository()


def main():
    args = Arguments().get_args()
    os_name = args.os_name
    release = args.release_name
    note = args.note
    hostname = args.hostname
    username = args.username
    password = args.password

    local_archive_name = f"depends_{os_name}{note}.tar.gz"
    remote_archive_name = f"packages_from_{os_name}{note}.tar.gz"
    dir_path = os.getcwd()

    # Создание объектов
    git_client = GitClient()
    git_client.clone(args.git_link, dir_path + "/distrib_dep")

    file_system = LocalFileSystem()
    file_system.search_main_packages(dir_path + "/distrib_dep/linux", dir_path + "/packages.txt", os_name,
                                     release_name=release)

    ssh_connection = SSHConnection(hostname, username, password)
    ssh_connection.connect()
    ssh_connection.run_command(f"rm -rf depends {remote_archive_name} && mkdir -p depends")

    package_manager = APTPackageManager()
    package_downloader = PackageDownloader(package_manager, ssh_connection)
    package_downloader.download_dependencies(file_system.listPackages)

    ssh_connection.run_command(f"cd ~/ && tar -czvf {remote_archive_name} depends")
    ssh_connection.sftp_open()
    ssh_connection.copy_from_remote(f"/home/echelon/{remote_archive_name}", f"{dir_path}/{remote_archive_name}")
    ssh_connection.close()

    file_system.wrie_info_packages(package_downloader.information_packages, f"{dir_path}/depends_{os_name}{note}.txt")
    file_system.unpack_archive(dir_path, remote_archive_name)

    local_repo = BuildRepository(local_archive_name, f"{dir_path}/depends", dir_path)
    local_repo.initial_repo()


if __name__ == "__main__":
    main()
