const fileManager = () => {
  return {
    activeTab: "files",
    showUploadModal: false,
    showNewFolderModal: false,
    showDeleteModal: false,
    showPreviewModal: false,
    deletePath: "",
    isDragging: false,
    selectedFiles: [],
    previewFilePath: "",
    previewFileName: "",
    previewType: "",
    previewMimeType: "",
    previewContent: "",
    previewLoading: false,

    handleFileSelect(event) {
      this.selectedFiles = Array.from(event.target.files);
      console.log("Files selected:", this.selectedFiles.length);
    },

    handleFileDrop(event) {
      this.isDragging = false;
      const files = event.dataTransfer.files;
      if (files.length > 0) {
        document.getElementById("file-upload").files = files;
        this.selectedFiles = Array.from(files);
        console.log("Files dropped:", this.selectedFiles.length);
      }
    },

    formatFileSize(size) {
      const units = ["B", "KB", "MB", "GB", "TB"];
      let unitIndex = 0;

      while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024;
        unitIndex++;
      }

      return `${size.toFixed(1)} ${units[unitIndex]}`;
    },

    openPreview(path, name) {
      this.previewFilePath = path;
      this.previewFileName = name;
      this.previewLoading = true;
      this.showPreviewModal = true;

      // Determine file type for preview
      const extension = name.split(".").pop().toLowerCase();
      const imageExts = ["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"];
      const videoExts = ["mp4", "webm", "ogg", "mov", "avi", "mkv"];
      const textExts = [
        "txt",
        "md",
        "html",
        "css",
        "js",
        "json",
        "xml",
        "csv",
        "py",
        "java",
        "c",
        "cpp",
        "h",
        "sh",
        "bat",
        "ps1",
        "log",
      ];

      if (imageExts.includes(extension)) {
        this.previewType = "image";
        this.previewMimeType = `image/${
          extension === "jpg" ? "jpeg" : extension
        }`;
        this.previewLoading = false;
      } else if (videoExts.includes(extension)) {
        this.previewType = "video";
        this.previewMimeType = `video/${
          extension === "mov" ? "quicktime" : extension
        }`;
        this.previewLoading = false;

        // Initialize video player once it's loaded
        this.$nextTick(() => {
          if (this.$refs.videoPlayer) {
            this.$refs.videoPlayer.load();
          }
        });
      } else if (extension === "pdf") {
        this.previewType = "pdf";
        this.previewMimeType = "application/pdf";
        this.previewLoading = false;
      } else if (textExts.includes(extension)) {
        this.previewType = "text";

        // Fetch text content
        fetch(`/preview?path=${encodeURIComponent(path)}`)
          .then((response) => {
            if (!response.ok) {
              throw new Error("Failed to fetch file content");
            }
            return response.text();
          })
          .then((content) => {
            // For certain file types, add syntax highlighting
            if (["html", "xml"].includes(extension)) {
              this.previewContent = this.escapeHtml(content);
            } else {
              this.previewContent = this.escapeHtml(content);
            }
            this.previewLoading = false;
          })
          .catch((error) => {
            console.error("Error previewing file:", error);
            this.previewType = "unsupported";
            this.previewLoading = false;
          });
      } else {
        this.previewType = "unsupported";
        this.previewLoading = false;
      }
    },

    escapeHtml(text) {
      return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    },
  };
};
