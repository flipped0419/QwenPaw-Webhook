const { React, antd } = window.QwenPaw.host;
const h = React.createElement;
const {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} = antd;

const pluginId = "qwenpaw-webhook";
const routeId = "qwenpaw-webhook.targets";

function t(locale, zh, en) {
  return locale && locale.startsWith("zh") ? zh : en;
}

async function api(path, options = {}) {
  const response = await window.QwenPaw.host.fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    throw new Error((data && (data.detail || data.message)) || `HTTP ${response.status}`);
  }
  return data || {};
}

function safeEndpoint(url) {
  const value = String(url || "").trim();
  if (!value) return "-";
  if (value.includes("${")) return "${ENV…}";
  try {
    const parsed = new URL(value);
    return `${parsed.protocol}//${parsed.host}/…`;
  } catch {
    return "<configured>";
  }
}

function jsonText(value, fallback) {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return fallback;
  }
}

function parseJson(text, label) {
  const value = String(text || "").trim();
  if (!value) return {};
  try {
    return JSON.parse(value);
  } catch (error) {
    throw new Error(`${label}: ${error.message}`);
  }
}

function TargetEditor({ open, target, onCancel, onSaved, locale }) {
  const [form] = Form.useForm();
  const editing = !!target;
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    if (target) {
      form.setFieldsValue({
        alias: target.alias,
        url: target.config.url || "",
        method: target.config.method || "POST",
        format: target.config.format || "json",
        timeout: target.config.timeout ?? 15,
        verify_tls: target.config.verify_tls !== false,
        headers_json: jsonText(target.config.headers || {}, "{}"),
        payload_text: jsonText(
          target.config.payload,
          JSON.stringify({ title: "{{title}}", content: "{{content}}", data: "{{data}}" }, null, 2),
        ),
      });
    } else {
      form.resetFields();
      form.setFieldsValue({
        method: "POST",
        format: "json",
        timeout: 15,
        verify_tls: true,
        headers_json: "{}",
        payload_text: JSON.stringify(
          { title: "{{title}}", content: "{{content}}", data: "{{data}}" },
          null,
          2,
        ),
      });
    }
  }, [open, target]);

  const save = async () => {
    try {
      const values = await form.validateFields();
      const headers = parseJson(values.headers_json, t(locale, "请求头 JSON 无效", "Invalid headers JSON"));
      const format = values.format || "json";
      let payload;
      if (format === "text") {
        payload = String(values.payload_text || "{{content}}");
      } else {
        payload = parseJson(values.payload_text, t(locale, "Payload JSON 无效", "Invalid payload JSON"));
      }
      const config = {
        url: String(values.url || "").trim(),
        method: values.method || "POST",
        format,
        headers,
        payload,
        timeout: Number(values.timeout || 15),
        verify_tls: values.verify_tls !== false,
      };
      setSaving(true);
      await api(`/api/webhook/targets/${encodeURIComponent(values.alias)}`, {
        method: "PUT",
        body: JSON.stringify(config),
      });
      message.success(t(locale, "推送目标已保存", "Webhook target saved"));
      onSaved();
    } catch (error) {
      if (error && error.errorFields) return;
      message.error(error.message || String(error));
    } finally {
      setSaving(false);
    }
  };

  return h(
    Modal,
    {
      open,
      title: editing ? t(locale, "编辑推送目标", "Edit webhook target") : t(locale, "新增推送目标", "Add webhook target"),
      onCancel,
      onOk: save,
      confirmLoading: saving,
      width: 720,
      destroyOnClose: true,
    },
    h(
      Form,
      { form, layout: "vertical" },
      h(Form.Item, {
        name: "alias",
        label: t(locale, "目标别名", "Target alias"),
        rules: [
          { required: true, message: t(locale, "请输入目标别名", "Enter an alias") },
          { pattern: /^[A-Za-z0-9_.-]{1,64}$/, message: t(locale, "仅允许字母、数字、点、下划线和短横线", "Use letters, numbers, '.', '_' or '-'") },
        ],
        children: h(Input, { disabled: editing, placeholder: "notify" }),
      }),
      h(Form.Item, {
        name: "url",
        label: "Webhook URL",
        rules: [{ required: true, message: t(locale, "请输入 Webhook URL", "Enter a webhook URL") }],
        extra: t(locale, "可以使用 ${ENV_NAME} 引用环境变量。", "You can reference environment variables with ${ENV_NAME}."),
        children: h(Input.Password, { placeholder: "https://example.com/webhook" }),
      }),
      h(
        Space,
        { size: 16, wrap: true, style: { width: "100%" } },
        h(Form.Item, {
          name: "method",
          label: "Method",
          style: { minWidth: 150 },
          children: h(Select, {
            options: ["POST", "PUT", "PATCH"].map((value) => ({ value, label: value })),
          }),
        }),
        h(Form.Item, {
          name: "format",
          label: "Format",
          style: { minWidth: 150 },
          children: h(Select, {
            options: [
              { value: "json", label: "JSON" },
              { value: "form", label: "Form" },
              { value: "text", label: "Text" },
            ],
          }),
        }),
        h(Form.Item, {
          name: "timeout",
          label: t(locale, "超时（秒）", "Timeout (seconds)"),
          children: h(InputNumber, { min: 1, max: 120 }),
        }),
        h(Form.Item, {
          name: "verify_tls",
          label: t(locale, "验证 TLS", "Verify TLS"),
          valuePropName: "checked",
          children: h(Switch),
        }),
      ),
      h(Form.Item, {
        name: "headers_json",
        label: t(locale, "请求头（JSON）", "Headers (JSON)"),
        extra: t(locale, "敏感值建议使用 ${ENV_NAME}，不要直接写进仓库。", "Use ${ENV_NAME} for secrets instead of committing them to source."),
        children: h(Input.TextArea, { rows: 5, spellCheck: false }),
      }),
      h(Form.Item, {
        name: "payload_text",
        label: t(locale, "Payload 模板", "Payload template"),
        extra: t(locale, "支持 {{title}}、{{content}}、{{data}}、{{data_json}}。Text 格式直接填写文本模板。", "Supports {{title}}, {{content}}, {{data}}, and {{data_json}}. For Text format, enter a text template."),
        children: h(Input.TextArea, { rows: 9, spellCheck: false }),
      }),
    ),
  );
}

function WebhookTargetsPage() {
  const locale = window.QwenPaw.host.useLocale();
  const [loading, setLoading] = React.useState(true);
  const [targets, setTargets] = React.useState([]);
  const [path, setPath] = React.useState("");
  const [editorOpen, setEditorOpen] = React.useState(false);
  const [editing, setEditing] = React.useState(null);
  const [testing, setTesting] = React.useState("");

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const data = await api("/api/webhook/targets");
      const rows = Object.entries(data.targets || {}).map(([alias, config]) => ({ alias, config }));
      rows.sort((a, b) => a.alias.localeCompare(b.alias));
      setTargets(rows);
      setPath(data.path || "");
    } catch (error) {
      message.error(error.message || String(error));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const remove = async (alias) => {
    try {
      await api(`/api/webhook/targets/${encodeURIComponent(alias)}`, { method: "DELETE" });
      message.success(t(locale, "推送目标已删除", "Webhook target deleted"));
      await load();
    } catch (error) {
      message.error(error.message || String(error));
    }
  };

  const testTarget = async (alias) => {
    setTesting(alias);
    try {
      const result = await api(`/api/webhook/targets/${encodeURIComponent(alias)}/test`, {
        method: "POST",
        body: JSON.stringify({
          title: "QwenPaw Webhook test",
          content: t(locale, "这是一条来自 QwenPaw Webhook 插件的测试消息。", "This is a test message from the QwenPaw Webhook plugin."),
          data: { test: true },
        }),
      });
      message.success(`HTTP ${result.status_code}`);
    } catch (error) {
      message.error(error.message || String(error));
    } finally {
      setTesting("");
    }
  };

  const columns = [
    {
      title: t(locale, "别名", "Alias"),
      dataIndex: "alias",
      key: "alias",
      render: (value) => h(Typography.Text, { code: true }, value),
    },
    {
      title: t(locale, "目标", "Endpoint"),
      key: "url",
      render: (_, row) => h(Typography.Text, { type: "secondary" }, safeEndpoint(row.config.url)),
    },
    {
      title: "Method",
      key: "method",
      width: 100,
      render: (_, row) => h(Tag, null, row.config.method || "POST"),
    },
    {
      title: "Format",
      key: "format",
      width: 100,
      render: (_, row) => h(Tag, null, (row.config.format || "json").toUpperCase()),
    },
    {
      title: t(locale, "操作", "Actions"),
      key: "actions",
      width: 250,
      render: (_, row) => h(
        Space,
        null,
        h(Button, {
          size: "small",
          onClick: () => {
            setEditing(row);
            setEditorOpen(true);
          },
          children: t(locale, "编辑", "Edit"),
        }),
        h(Button, {
          size: "small",
          loading: testing === row.alias,
          onClick: () => testTarget(row.alias),
          children: t(locale, "测试", "Test"),
        }),
        h(
          Popconfirm,
          {
            title: t(locale, "删除这个推送目标？", "Delete this webhook target?"),
            onConfirm: () => remove(row.alias),
            okText: t(locale, "删除", "Delete"),
            cancelText: t(locale, "取消", "Cancel"),
          },
          h(Button, { size: "small", danger: true }, t(locale, "删除", "Delete")),
        ),
      ),
    },
  ];

  return h(
    "div",
    { style: { padding: 24, maxWidth: 1200, margin: "0 auto" } },
    h(
      Space,
      { direction: "vertical", size: 16, style: { width: "100%" } },
      h(
        "div",
        { style: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16 } },
        h(
          "div",
          null,
          h(Typography.Title, { level: 2, style: { marginBottom: 4 } }, "Webhook"),
          h(Typography.Text, { type: "secondary" }, t(locale, "管理 QwenPaw 的出站推送目标。", "Manage outbound webhook targets for QwenPaw.")),
        ),
        h(Button, {
          type: "primary",
          onClick: () => {
            setEditing(null);
            setEditorOpen(true);
          },
          children: t(locale, "新增目标", "Add target"),
        }),
      ),
      h(Alert, {
        type: "info",
        showIcon: true,
        message: t(locale, "配置保存在 QwenPaw Secret 目录", "Configuration is stored in the QwenPaw Secret directory"),
        description: path || t(locale, "尚未创建配置文件。", "The target file has not been created yet."),
      }),
      h(
        Card,
        { bodyStyle: { padding: 0 } },
        h(Table, {
          rowKey: "alias",
          columns,
          dataSource: targets,
          loading,
          pagination: false,
          locale: { emptyText: t(locale, "还没有推送目标，点击“新增目标”创建一个。", "No webhook targets yet. Click Add target to create one.") },
        }),
      ),
    ),
    h(TargetEditor, {
      open: editorOpen,
      target: editing,
      locale,
      onCancel: () => setEditorOpen(false),
      onSaved: async () => {
        setEditorOpen(false);
        await load();
      },
    }),
  );
}

window.QwenPaw.menu.add(pluginId, {
  id: routeId,
  label: "Webhook",
  route: routeId,
  location: "primary.settings",
  order: 75,
});

window.QwenPaw.route.add(pluginId, {
  id: routeId,
  path: "/webhook",
  component: WebhookTargetsPage,
});
