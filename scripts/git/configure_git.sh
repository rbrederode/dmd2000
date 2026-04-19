# Setup email and username for git

echo "=== 1. Configure Git ==="
git config --global user.name "Ray Brederode"
git config --global user.email "rbrederode@yahoo.co.uk"

echo "=== 2. Set Git to use rebase by default when pulling ==="
git config --global pull.rebase true

echo "=== 3. Set Git to use the 'art-sdk' branch as the default initial branch name ==="
git fetch --all
git branch -a
if git show-ref --verify --quiet refs/heads/art-sdk; then
    echo "Branch 'art-sdk' already exists locally."
else
    if git ls-remote --heads origin art-sdk | grep -q "refs/heads/art-sdk"; then
        echo "Branch 'art-sdk' exists on remote. Checking it out locally."
        git checkout -b art-sdk origin/art-sdk
    else
        echo "Branch 'art-sdk' does not exist on remote. Creating it locally."
        git checkout -b art-sdk
    fi
fi
git config --global init.defaultBranch art-sdk