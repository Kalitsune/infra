default: plan

init:
    tofu init

plan:
    tofu plan

apply:
    tofu apply

destroy:
    tofu destroy

fmt:
    tofu fmt -recursive

validate: fmt
    tofu validate
