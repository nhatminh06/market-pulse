resource "minio_s3_bucket" "warehouse" {
  bucket = "warehouse"
  acl    = "private"
}

# Least-privilege policy: the pipeline account can only touch the warehouse bucket.
resource "minio_iam_policy" "pipeline_rw" {
  name = "pipeline-readwrite"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      Resource = [
        "arn:aws:s3:::${minio_s3_bucket.warehouse.bucket}",
        "arn:aws:s3:::${minio_s3_bucket.warehouse.bucket}/*"
      ]
    }]
  })
}

resource "minio_iam_user" "pipeline" {
  name          = "pipeline-svc"
  force_destroy = true
}

resource "minio_iam_user_policy_attachment" "attach" {
  user_name   = minio_iam_user.pipeline.name
  policy_name = minio_iam_policy.pipeline_rw.name
}