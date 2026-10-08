{{- define "streamscale.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- define "streamscale.env" -}}
- name: KAFKA_BOOTSTRAP_SERVERS
  value: {{ printf "%s-kafka:9092" .Release.Name | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgres.existingSecret }}
      key: DATABASE_URL
{{- end -}}
{{- define "streamscale.security" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
{{- end -}}
{{- define "streamscale.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}
