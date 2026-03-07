"""
Request/Response Validation Schemas using Marshmallow.

Validates incoming JSON payloads before processing. Ensures required
fields are present and types are correct, returning clear error messages.
"""

from marshmallow import Schema, fields, validate, validates_schema, ValidationError


class LineItemSchema(Schema):
    description = fields.Str(required=True)
    hsn_code = fields.Str(load_default="")
    quantity = fields.Float(required=True)
    unit = fields.Str(load_default="pcs")
    rate = fields.Float(required=True)
    amount = fields.Float(required=True)
    gst_rate = fields.Float(load_default=18.0)
    gst_amount = fields.Float(load_default=0.0)
    total = fields.Float(load_default=0.0)


class CompanySchema(Schema):
    name = fields.Str(required=True)
    address = fields.Str(load_default="")
    phone = fields.Str(load_default="")
    email = fields.Str(load_default="")
    gstin = fields.Str(load_default="")


class PartySchema(Schema):
    name = fields.Str(required=True)
    address = fields.Str(load_default="")
    gstin = fields.Str(load_default="")
    contact = fields.Str(load_default="")
    state = fields.Str(load_default="")
    state_code = fields.Str(load_default="")


class BankDetailsSchema(Schema):
    bank_name = fields.Str(load_default="")
    account_number = fields.Str(load_default="")
    ifsc = fields.Str(load_default="")
    branch = fields.Str(load_default="")


class SinglePdfRequestSchema(Schema):
    doc_type = fields.Str(
        required=True,
        validate=validate.OneOf(["purchase_order", "invoice"]),
    )
    data = fields.Dict(required=True)

    @validates_schema
    def validate_data_has_required_fields(self, data, **kwargs):
        doc_data = data.get("data", {})
        if "document_number" not in doc_data:
            raise ValidationError("data.document_number is required")
        if "line_items" not in doc_data:
            raise ValidationError("data.line_items is required")
        if not isinstance(doc_data["line_items"], list):
            raise ValidationError("data.line_items must be a list")
        if len(doc_data["line_items"]) == 0:
            raise ValidationError("data.line_items must not be empty")


class BulkPdfRequestSchema(Schema):
    documents = fields.List(
        fields.Dict(),
        required=True,
        validate=validate.Length(min=1, max=100),
    )

    @validates_schema
    def validate_documents(self, data, **kwargs):
        for i, doc in enumerate(data.get("documents", [])):
            if "doc_type" not in doc:
                raise ValidationError(f"documents[{i}].doc_type is required")
            if doc["doc_type"] not in ("purchase_order", "invoice"):
                raise ValidationError(
                    f"documents[{i}].doc_type must be 'purchase_order' or 'invoice'"
                )
            if "data" not in doc:
                raise ValidationError(f"documents[{i}].data is required")
            doc_data = doc["data"]
            if "document_number" not in doc_data:
                raise ValidationError(f"documents[{i}].data.document_number is required")
            if "line_items" not in doc_data or not doc_data["line_items"]:
                raise ValidationError(f"documents[{i}].data.line_items is required and non-empty")
