{{- define "hermes.name" -}}hermes-smart-router{{- end -}}
{{- define "hermes.labels" -}}
app.kubernetes.io/name: {{ include "hermes.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
