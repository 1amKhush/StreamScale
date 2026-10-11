{{- define "console.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- define "console.env" -}}
- name: KAFKA_BOOTSTRAP_SERVERS
  value: {{ printf "%s-kafka:9092" .Values.pipeline.release | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.pipeline.credentialsSecret }}
      key: DATABASE_URL
- name: POD_NAME
  valueFrom:
    fieldRef: {fieldPath: metadata.name}
- name: CONSOLE_IMAGE
  value: {{ include "console.image" . | quote }}
{{- end -}}
{{- define "console.security" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
{{- end -}}
{{- define "console.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}
