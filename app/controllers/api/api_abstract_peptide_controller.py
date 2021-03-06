import sys
import orjson
import math

from flask import jsonify, Response
from sqlalchemy import func, distinct, and_, select, desc, asc
from sqlalchemy.orm import sessionmaker, aliased

from macpepdb.proteomics.mass.convert import to_int as mass_to_int, to_float as mass_to_float
from macpepdb.proteomics.modification import Modification, ModificationPosition
from macpepdb.proteomics.amino_acid import AminoAcid
from macpepdb.proteomics.modification_collection import ModificationCollection
from macpepdb.models.modified_peptide_where_clause_builder import ModifiedPeptideWhereClauseBuilder
from macpepdb.models.protein import Protein
from macpepdb.models.taxonomy import Taxonomy, TaxonomyRank
from macpepdb.models.peptide import Peptide
from macpepdb.models.associacions import proteins_peptides as proteins_peptides_table


from app import macpepdb, app
from ..application_controller import ApplicationController

class ApiAbstractPeptideController(ApplicationController):
    SUPPORTED_OUTPUTS = ['application/json', 'application/octet-stream', 'text/plain']
    SUPPORTED_ORDER_COLUMNS = ['mass', 'length', 'sequence', 'number_of_missed_cleavages']
    SUPPORTED_ORDER_DIRECTIONS = ['asc', 'desc']
    FASTA_SEQUENCE_LEN = 60

    @staticmethod
    def _search(request):
        errors = []
        data = request.get_json()

        include_count = False
        if 'include_count' in data and isinstance(data['include_count'], bool):
            include_count = data['include_count']

        order_by = None
        order_direction = asc
        if 'order_by' in data:
            if isinstance(data['order_by'], str) and data['order_by'] in ApiAbstractPeptideController.SUPPORTED_ORDER_COLUMNS:
                if data['order_by'] == 'mass':
                    order_by = 'weight'
                else:
                    order_by = data['order_by']
            else:
                errors.append(f"'order_by' must be a string with one of following values: {', '.join(ApiAbstractPeptideController.SUPPORTED_ORDER_COLUMNS)}")
        

        if 'order_direction' in data:
            if isinstance(data['order_direction'], str) and data['order_direction'] in ApiAbstractPeptideController.SUPPORTED_ORDER_DIRECTIONS:
                if data['order_direction'] == 'desc':
                    order_direction = desc
            else:
                errors.append(f"'order_direction' must be a string with one of following values: {', '.join(ApiAbstractPeptideController.SUPPORTED_ORDER_DIRECTIONS)}")

        # Get accept header (default 'application/json'), split by ',' in case multiple mime types where supported and take the first one
        output_style = request.headers.get('accept', default=ApiAbstractPeptideController.SUPPORTED_OUTPUTS[0]).split(',')[0].strip()
        # If the mime type is not supported set default one
        if not output_style in ApiAbstractPeptideController.SUPPORTED_OUTPUTS:
            output_style = ApiAbstractPeptideController.SUPPORTED_OUTPUTS[0]

        # validate int attributes
        for attribute in  ["lower_precursor_tolerance_ppm", "upper_precursor_tolerance_ppm", "variable_modification_maximum"]:
            if not attribute in data or data[attribute] == None:
                errors.append("you have to specify {}".format(attribute))
                continue
            if not isinstance(data[attribute], int):
                errors.append("'{}' has to be int".format(attribute))
                continue
            if data[attribute] < 0:
                errors.append("'{}' must be greater or equals 0".format(attribute))

        modifications = []
        if "modifications" in data:
            if isinstance(data["modifications"], list):
                for idx, modification_attributes in enumerate(data["modifications"]):
                    if isinstance(modification_attributes, dict):
                        accession_and_name = "onlinemod:{}".format(idx)
                        try:
                            modification_attributes['accession'] = accession_and_name
                            modification_attributes['name'] = accession_and_name
                            modification_attributes['delta'] = mass_to_int(modification_attributes['delta'])
                            modifications.append(Modification.from_dict(modification_attributes))
                        except Exception as e:
                            errors.append("modification {} is not valid: {}".format(modification_attributes, e))
                    else:
                        errors.append("modifications {} has to be of type dict".format(modification_attributes))
            else:
                errors.append("modifications has to be of type list")
        
        try:
            modification_collection = ModificationCollection(modifications)
        except Exception as e:
            errors.append("{}".format(e))
        
        if not len(errors):
            if "precursor" in data:
                if isinstance(data["precursor"], float) or isinstance(data["precursor"], int):

                    where_clause_builder = ModifiedPeptideWhereClauseBuilder(
                        modification_collection, 
                        mass_to_int(data["precursor"]),
                        data["lower_precursor_tolerance_ppm"],
                        data["upper_precursor_tolerance_ppm"],
                        data["variable_modification_maximum"]
                    )
                    where_clause = where_clause_builder.build(Peptide)

                    count_query = select([func.count(distinct(Peptide.id))]).where(where_clause).select_from(Peptide.__table__)
                    peptides_query = None
                    if not output_style == ApiAbstractPeptideController.SUPPORTED_OUTPUTS[2]:
                        peptides_query = Peptide.__table__.select(where_clause)
                    else:
                        peptides_query = select([Peptide.id, Peptide.sequence]).where(where_clause).distinct()

                    # Array for conditions on proteins
                    protein_conditions = []
                    if "taxonomy_id" in data:
                        if isinstance(data["taxonomy_id"], int):
                            # Recursively select all taxonomies below the given one
                            recursive_query = select(Taxonomy.__table__.columns).where(Taxonomy.id == data["taxonomy_id"]).cte(recursive=True)
                            parent_taxonomies = recursive_query.alias()
                            child_taxonomies = Taxonomy.__table__.alias()
                            sub_taxonomies = recursive_query.union_all(select(child_taxonomies.columns).where(child_taxonomies.c.parent_id == parent_taxonomies.c.id))
                            sub_species_id_query = select([sub_taxonomies.c.id])

                            with macpepdb.connect() as connection:
                                sub_species_ids = [row[0] for row in connection.execute(sub_species_id_query).fetchall()]
                            
                            if len(sub_species_ids) == 1:
                                protein_conditions.append(Protein.taxonomy_id == sub_species_ids[0])
                            elif len(sub_species_ids) > 1:
                                protein_conditions.append(Protein.taxonomy_id.in_(sub_species_ids))
                        else:
                            errors.append("taxonomy_id has to be of type int")

                    if "proteome_id" in data:
                        if isinstance(data["proteome_id"], str):
                            protein_conditions.append(Protein.proteome_id == data["proteome_id"])
                        else:
                            errors.append("proteome_id has to be of type string")

                    if "is_reviewed" in data:
                        if isinstance(data["is_reviewed"], bool):
                            protein_conditions.append(Protein.is_reviewed == data["is_reviewed"])
                        else:
                            errors.append("is_reviewed has to be of type int")

                    if len(protein_conditions):
                        # Concatenate conditions with and
                        protein_where_clause = and_(*protein_conditions)
                        # Rebuild count query
                        inner_count_query = select([Peptide.id]).where(where_clause).select_from(Peptide.__table__).alias('mass_specific_peptides')
                        protein_join = inner_count_query.join(proteins_peptides_table, proteins_peptides_table.c.peptide_id == inner_count_query.c.id)
                        protein_join = protein_join.join(Protein.__table__, Protein.id == proteins_peptides_table.c.protein_id)
                        count_query = select([func.count(distinct(inner_count_query.c.id))]).select_from(protein_join).where(protein_where_clause)

                        # Create alais for the inner query
                        inner_peptide_query = peptides_query.alias('mass_specific_peptides')
                        # Join innder query with proteins
                        protein_join = inner_peptide_query.join(proteins_peptides_table, proteins_peptides_table.c.peptide_id == inner_peptide_query.c.id)
                        protein_join = protein_join.join(Protein.__table__, Protein.id == proteins_peptides_table.c.protein_id)
                        # Create select around the inner query
                        peptides_query = select(inner_peptide_query.columns).select_from(protein_join).where(protein_where_clause)

                    # Sort by weight
                    if order_by and not output_style == ApiAbstractPeptideController.SUPPORTED_OUTPUTS[2]:
                        peptides_query = peptides_query.order_by(order_direction(peptides_query.c[order_by]))

                    peptides_query = peptides_query.distinct()

                    # Note about offset and limit: It is much faster to fetch data from server and discard rows below the offset and stop the fetching when the limit is reached, instead of applying LIMIT and OFFSET directly to the query.
                    # Even on high offsets, which discards a lot of rows, this approach is faster.
                    # Curl shows the diffences: curl -o foo.json --header "Content-Type: application/json" --request POST --data '{"include_count":true,"offset":0,"limit":50,"modifications":[{"amino_acid":"C","position":"anywhere","is_static":true,"delta":57.021464}],"lower_precursor_tolerance_ppm":5,"upper_precursor_tolerance_ppm":5,"variable_modification_maximum":0,"order":true,"precursor":859.49506802369}' http://localhost:3000/api/peptides/search
                    # Applying OFFSET and LIMIT to query: 49 - 52 seconds
                    # Discarding rows which are below the offset and stop the fetching early: a few hundred miliseconds (not printed by curl).
                    offset = 0
                    limit = math.inf
                    if "limit" in data:
                        if isinstance(data["limit"], int):
                            limit = data["limit"]
                        else:
                            errors.append("limit has to be of type int")
                    if "offset" in data:
                        if isinstance(data["offset"], int):
                            offset = data["offset"]
                        else:
                            errors.append("offset has to be of type int")

                else:
                    errors.append("precursor has to be a int/float")
            else:
                errors.append("you have to specify a precursor")

        if len(errors):
            return jsonify({
                "errors": errors
            }), 422

        if output_style ==  ApiAbstractPeptideController.SUPPORTED_OUTPUTS[0]:
            def generate_json_stream(peptides_query, peptide_count_query, include_count: bool, offset: int, limit: int):
                """
                Serialize the given peptides as JSON objects, structure: {'result_key': [peptide_json, ...]}
                @param peptides_query The query for peptides
                @param peptide_count_query The query to count the peptides.
                @param result_key Name of the result key within the JSON-objecte, e.g. {'result_key': [peptide_json, ...]}
                @param include_count Boolean to include count in the results, e.g. {'count': 0, 'result_key': [peptide_json, ...]}
                @param offset Result offset
                @param limit Result limit
                """
                with macpepdb.connect() as db_connection:
                    # Open a JSON object
                    yield b"{"
                    # Check if there are pepritdes
                    # Add count to open object
                    if include_count:
                        peptide_count = db_connection.execute(peptide_count_query).fetchone()[0]
                        yield f"\"count\":{peptide_count},".encode()
                    yield f"\"peptides\":[".encode()
                    # Create cursor to stream results
                    peptides_cursor = db_connection.execution_options(stream_results=True).execute(peptides_query)
                    is_first_chunk = True
                    peptide_counter = 0
                    written_peptide_counter = 0
                    while written_peptide_counter < limit:
                        # Fetch 10000 results
                        peptides_chunk = peptides_cursor.fetchmany(10000)
                        # Stop loop if no results were fetched
                        if not peptides_chunk:
                            break
                        # If this is not the first chunk, append a ',', because the last peptide of the previous chunk does not has one appended
                        if not is_first_chunk:
                            yield b","
                        # Create iterator 
                        peptides_chunk_iter = peptides_chunk.__iter__()
                        # Get first result
                        previous_peptide_row = next(peptides_chunk_iter)
                        # Iterate over the remaining rows in this chunk
                        for peptide_row in peptides_chunk_iter:
                            # Increase counter for each peptide
                            peptide_counter += 1
                            # Write peptide to stream if peptide counter is larger than offset
                            if peptide_counter > offset:
                                # Write the previous peptide to stream ...
                                peptide_dict = {str(key): value for key, value in previous_peptide_row.items()}
                                peptide_dict["mass"] = mass_to_float(peptide_dict.pop("weight"))
                                peptide_dict['peff_notation_of_modifications'] = ''
                                yield orjson.dumps(peptide_dict)
                                # ... and append 
                                yield b","
                                # Increase written peptides
                                written_peptide_counter += 1
                            # Mark the current peptide as previous peptide for next iteration
                            previous_peptide_row = peptide_row
                            # Break for-loop if limit - 1 peptides where written and stop result streaming. After the last peptide is written (after the for loop) streaming is stopped.
                            if written_peptide_counter == limit - 1:
                                break
                        # Increase counter for last peptide
                        peptide_counter += 1
                        if peptide_counter > offset:
                            # Now write the last peptide without a ',' at the end
                            peptide_dict = {str(key): value for key, value in previous_peptide_row.items()}
                            peptide_dict["mass"] = mass_to_float(peptide_dict.pop("weight"))
                            peptide_dict['peff_notation_of_modifications'] = ''
                            yield orjson.dumps(peptide_dict)
                            # Increase written peptides
                            written_peptide_counter += 1
                            is_first_chunk = False
                    # Close array and object
                    yield b"]}"
            # Send stream
            return Response(generate_json_stream(peptides_query, count_query, include_count, offset, limit), content_type=f"{ApiAbstractPeptideController.SUPPORTED_OUTPUTS[0]}; charset=utf-8")
        elif output_style == ApiAbstractPeptideController.SUPPORTED_OUTPUTS[1]:
            def generate_octet_stream(peptides_query, offset: int, limit: int):
                """
                This will generate a stream of JSON-formatted peptides per line. Each JSON-string is bytestring.
                @param peptides_query The query for peptides
                @param offset Result offset
                @param limit Result limit
                """
                with macpepdb.connect() as db_connection:
                    # Create cursor
                    peptides_cursor = db_connection.execution_options(stream_results=True).execute(peptides_query)
                    is_first_chunk = True
                    peptide_counter = 0
                    written_peptide_counter = 0
                    while written_peptide_counter < limit:
                        # Fetch 10000 results
                        peptides_chunk = peptides_cursor.fetchmany(10000)
                        # Stop loop if no results were fetched
                        if not len(peptides_chunk):
                            break
                        # If this is not the first chunk, append a new line, because the last peptide of the previous chunk does not has one appended
                        if not is_first_chunk:
                            yield b"\n"
                        # Create iterator 
                        peptides_chunk_iter = peptides_chunk.__iter__()
                        # Get first result
                        previous_peptide_row = next(peptides_chunk_iter)
                        # Iterate over the remaining rows in this chunk
                        for peptide_row in peptides_chunk_iter:
                            # Increase counter for each peptide
                            peptide_counter += 1
                            # Write peptide to stream if peptide counter is larger than offset
                            if peptide_counter > offset:
                                # Write the previous peptide to stream ...
                                peptide_dict = {str(key): value for key, value in previous_peptide_row.items()}
                                peptide_dict["mass"] = mass_to_float(peptide_dict.pop("weight"))
                                peptide_dict['peff_notation_of_modifications'] = ''
                                yield orjson.dumps(peptide_dict)
                                # ... and append 
                                yield b"\n"
                                # Increase written peptides
                                written_peptide_counter += 1
                            # Mark the current peptide as previous peptide for next iteration
                            previous_peptide_row = peptide_row
                            # Break for-loop if limit - 1 peptides where written. After the last peptide is written (after the for loop) streaming is stopped (while loop).
                            if written_peptide_counter == limit - 1:
                                break
                        # Increase counter for the last peptide in chunk
                        peptide_counter += 1
                        # Now write the last peptide without a '\n' at the end
                        if peptide_counter > offset:
                            peptide_dict = {str(key): value for key, value in previous_peptide_row.items()}
                            peptide_dict["mass"] = mass_to_float(peptide_dict.pop("weight"))
                            peptide_dict['peff_notation_of_modifications'] = ''
                            yield orjson.dumps(peptide_dict)
                            # Increase written peptides
                            written_peptide_counter += 1
                            is_first_chunk = False
            return Response(generate_octet_stream(peptides_query, offset, limit), content_type=ApiAbstractPeptideController.SUPPORTED_OUTPUTS[1])
        elif output_style == ApiAbstractPeptideController.SUPPORTED_OUTPUTS[2]:
            def generate_txt_stream(peptide_query, offset: int, limit: int):
                """
                This will generate a stream of peptides in fasta format.
                @param peptides_query The query for peptides
                @params peptice_class Class of the peptides (Peptide/Decoy)
                @param offset Result offset
                @param limit Result limit
                """
                with macpepdb.connect() as db_connection:
                    # Create cursor
                    peptides_cursor = db_connection.execution_options(stream_results=True).execute(peptides_query)
                    is_first_chunk = True
                    peptide_counter = 0
                    written_peptide_counter = 0
                    while written_peptide_counter < limit:
                        # Fetch 10000 results
                        peptides_chunk = peptides_cursor.fetchmany(10000)
                        # Stop loop if no results were fetched
                        if not len(peptides_chunk):
                            break
                        # If this is not the first chunk, append a new line, because the last peptide of the previous chunk does not has one appended
                        if not is_first_chunk:
                            yield "\n"
                        # Create iterator 
                        peptides_chunk_iter = peptides_chunk.__iter__()
                        # Get first result
                        previous_peptide_row = next(peptides_chunk_iter)
                        # Iterate over the remaining rows in this chunk
                        for row_idx, peptide_row in enumerate(peptides_chunk_iter):
                            # Increase counter for each peptide
                            peptide_counter += 1
                            # Add newline to stream, if this is not the first peptide
                            if row_idx > 0:
                                yield "\n"
                            # Write peptide to stream if peptide counter is larger than offset
                            if peptide_counter > offset:
                                # Write the previous peptide to stream ...
                                yield f">lcl|PEPTIDE_{previous_peptide_row['id']}"
                                # Write sequence in chunks of 60 amino acids ...
                                seq_chunk_start = 0
                                while seq_chunk_start < len(previous_peptide_row['sequence']):
                                    # Write sequence from seq_chunk_start to seq_chunk_start+ApiAbstractPeptideController.FASTA_SEQUENCE_LEN (explicit end)
                                    yield f"\n{previous_peptide_row['sequence'][seq_chunk_start:seq_chunk_start+ApiAbstractPeptideController.FASTA_SEQUENCE_LEN]}"
                                    seq_chunk_start += ApiAbstractPeptideController.FASTA_SEQUENCE_LEN
                                # Increase written peptides
                                written_peptide_counter += 1
                            # Mark the current peptide as previous peptide for next iteration
                            previous_peptide_row = peptide_row
                            # Break for-loop if limit - 1 peptides where written and stop result streaming. After the last peptide is written (after the for loop) streaming is stopped.
                            if written_peptide_counter == limit - 1:
                                break
                        # Increase counter for last peptide
                        peptide_counter += 1
                        if peptide_counter > offset:
                            # Now write the last peptide without a new line at the end
                            yield f"\n>lcl|PEPTIDE_{previous_peptide_row['id']}"
                            # Write sequence in chunks of 60 amino acids ...
                            seq_chunk_start = 0
                            while seq_chunk_start < len(previous_peptide_row['sequence']):
                                # Write sequence from seq_chunk_start to seq_chunk_start+ApiAbstractPeptideController.FASTA_SEQUENCE_LEN (explicit end)
                                yield f"\n{previous_peptide_row['sequence'][seq_chunk_start:seq_chunk_start+ApiAbstractPeptideController.FASTA_SEQUENCE_LEN]}"
                                seq_chunk_start += ApiAbstractPeptideController.FASTA_SEQUENCE_LEN
                            # Increase written peptides
                            written_peptide_counter += 1
                            is_first_chunk = False
            return Response(generate_txt_stream(peptides_query, offset, limit), content_type=ApiAbstractPeptideController.SUPPORTED_OUTPUTS[2])
        
