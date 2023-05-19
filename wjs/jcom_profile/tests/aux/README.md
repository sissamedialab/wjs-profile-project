This folder collects auxiliary files used in some tests.

If these files are not referred to by any test, the can be discarded.

To clean up, from the folder where this README.md files resides, please run:
```sh
echo "Deleting old auxiliary files not used in tests any more."
for f in *
do
    if [[ "$f" != "README.md" ]]
    then
        grep -q -r -e "$f" ../ || rm -i "$f"
    fi
done
```
